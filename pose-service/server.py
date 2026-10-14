"""Pose estimation microservice — GPU-accelerated YOLO26x-Pose inference.

This service is fully self-contained: it processes the video, saves results
to GCS, and writes progress to GCS. The Flask app only needs to trigger it
and poll GCS for status. No background threads needed.
"""

import os
import json
import tempfile
import torch
import cv2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import storage
from ultralytics import YOLO

app = FastAPI(title="BachataAI Pose Service")

MODEL = YOLO("yolo26x-pose.pt")
GCS_CLIENT = None


def _gcs():
    global GCS_CLIENT
    if GCS_CLIENT is None:
        GCS_CLIENT = storage.Client()
    return GCS_CLIENT


class AnalyzeRequest(BaseModel):
    gcs_bucket: str = "bachataai-videos"
    gcs_blob: str
    video_id: int
    sample_every_n: int = 2
    resolution: int = 640


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gpu": torch.cuda.is_available(),
        "device": str(next(MODEL.model.parameters()).device),
    }


def _write_progress(bucket, video_id, data, force=False):
    """Write progress JSON to GCS. Returns False if cancelled (caller should stop)."""
    try:
        # Check separate cancel flag file
        if not force:
            cancel_blob = bucket.blob(f"cancel/{video_id}")
            if cancel_blob.exists():
                return False
        blob = bucket.blob(f"progress/{video_id}.json")
        blob.upload_from_string(json.dumps(data), content_type="application/json")
        return True
    except Exception:
        return True


def _run_analysis(req: AnalyzeRequest):
    """Process video end-to-end: inference → save poses to GCS → update progress."""
    bucket = _gcs().bucket(req.gcs_bucket)

    # Force-write initial progress to clear any old cancelled flag
    _write_progress(bucket, req.video_id, {
        "status": "running", "percent": 5, "step": "Downloading video..."
    }, force=True)

    # Download video
    blob = bucket.blob(req.gcs_blob)
    if not blob.exists():
        _write_progress(bucket, req.video_id, {
            "status": "error", "percent": 0, "step": f"Video file not found: {req.gcs_blob}"
        }, force=True)
        return

    suffix = os.path.splitext(req.gcs_blob)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        blob.download_to_filename(tmp_path)

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            _write_progress(bucket, req.video_id, {
                "status": "error", "percent": 0, "step": "Could not open video file"
            }, force=True)
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frames_to_process = total_frames // req.sample_every_n

        # Force-write to ensure we override any stale cancelled flag
        _write_progress(bucket, req.video_id, {
            "status": "running", "percent": 10,
            "step": f"Analyzing {frames_to_process} frames at {req.resolution}px..."
        }, force=True)

        frames = []
        frame_num = 0
        processed = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % req.sample_every_n == 0:
                h, w = frame.shape[:2]
                results = MODEL(frame, verbose=False, imgsz=req.resolution)

                persons = []
                if results and results[0].keypoints is not None:
                    kpts = results[0].keypoints
                    boxes = results[0].boxes
                    num_persons = len(kpts.xy) if kpts.xy is not None else 0

                    for p_idx in range(num_persons):
                        xy = kpts.xy[p_idx]
                        conf = kpts.conf[p_idx] if kpts.conf is not None else None
                        cx = 0.5
                        bbox_area = 0
                        track_id = -1
                        if boxes is not None and len(boxes.xyxy) > p_idx:
                            box = boxes.xyxy[p_idx]
                            cx = float((box[0] + box[2]) / 2) / w
                            bbox_area = float((box[2] - box[0]) * (box[3] - box[1])) / (w * h)
                            if boxes.id is not None and len(boxes.id) > p_idx:
                                track_id = int(boxes.id[p_idx])

                        person_kpts = {}
                        for i in range(len(xy)):
                            x_px, y_px = float(xy[i][0]), float(xy[i][1])
                            vis = float(conf[i]) if conf is not None else 1.0
                            person_kpts[str(i)] = {
                                "x": round(x_px / w, 5),
                                "y": round(y_px / h, 5),
                                "z": 0.0,
                                "visibility": round(vis, 3),
                            }
                        persons.append({"keypoints": person_kpts, "center_x": round(cx, 4), "bbox_area": round(bbox_area, 4), "track_id": track_id})

                # Filter out small bboxes (background people/audience)
                persons = [p for p in persons if p["bbox_area"] > 0.02]
                # Sort by distance to frame center (closest first)
                persons.sort(key=lambda p: abs(p["center_x"] - 0.5))

                frames.append({
                    "frame_num": frame_num,
                    "timestamp_sec": round(frame_num / fps, 4),
                    "persons": persons,
                    "keypoints": persons[0]["keypoints"] if persons else {},
                })

                processed += 1

                # Update progress + check cancel every 200 processed frames
                if processed % 200 == 0:
                    pct = 10 + int((processed / max(frames_to_process, 1)) * 75)
                    alive = _write_progress(bucket, req.video_id, {
                        "status": "running", "percent": min(pct, 85),
                        "step": f"Analyzed {processed}/{frames_to_process} frames ({min(pct, 85)}%)"
                    })
                    if not alive:
                        cap.release()
                        _write_progress(bucket, req.video_id, {
                            "status": "cancelled", "percent": 0, "step": "Cancelled"
                        }, force=True)
                        return {"status": "cancelled", "video_id": req.video_id}

            frame_num += 1

        cap.release()

        # Check cancel before saving
        alive = _write_progress(bucket, req.video_id, {
            "status": "running", "percent": 90,
            "step": f"Saving {len(frames)} frames to storage..."
        })
        if not alive:
            _write_progress(bucket, req.video_id, {
                "status": "cancelled", "percent": 0, "step": "Cancelled"
            }, force=True)
            return {"status": "cancelled", "video_id": req.video_id}

        pose_data = json.dumps({
            "frame_count": frame_num,
            "fps": round(fps, 2),
            "frames": frames,
        })

        pose_blob = bucket.blob(f"poses/{req.video_id}.json")
        pose_blob.upload_from_string(pose_data, content_type="application/json")

        # Write final status — force to ensure frontend knows job ended
        # Check if cancelled during processing
        cancel_blob = bucket.blob(f"cancel/{req.video_id}")
        if cancel_blob.exists():
            # Cancelled but pose was already saved — delete it
            try:
                pose_blob.delete()
            except Exception:
                pass
            _write_progress(bucket, req.video_id, {
                "status": "cancelled", "percent": 0, "step": "Cancelled"
            }, force=True)
        else:
            _write_progress(bucket, req.video_id, {
                "status": "done", "percent": 100,
                "step": f"Complete — {len(frames)} frames analyzed",
                "pose_gcs_path": f"poses/{req.video_id}.json",
                "pose_size_kb": len(pose_data) // 1024,
            }, force=True)

    except Exception as e:
        _write_progress(bucket, req.video_id, {
            "status": "error", "percent": 0, "step": str(e)
        }, force=True)
    finally:
        os.unlink(tmp_path)


@app.post("/analyze")
def analyze_video(req: AnalyzeRequest):
    """Runs analysis synchronously — blocks until done.
    Must block so Cloud Run keeps the container alive.
    Progress is written to GCS at gs://bucket/progress/<video_id>.json
    Results saved to gs://bucket/poses/<video_id>.json
    """
    _run_analysis(req)
    return {"status": "done", "video_id": req.video_id}
