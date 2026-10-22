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


class DetectRequest(BaseModel):
    gcs_bucket: str = "bachataai-videos"
    gcs_blob: str
    resolution: int = 640


class AnalyzeRequest(BaseModel):
    gcs_bucket: str = "bachataai-videos"
    gcs_blob: str
    video_id: int
    sample_every_n: int = 2
    resolution: int = 640
    selected_boxes: list | None = None  # [{x1,y1,x2,y2,label}] normalized 0-1


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


@app.post("/detect-first-frame")
def detect_first_frame(req: DetectRequest):
    """Run pose estimation on first frame — returns bboxes + keypoints for labeling."""
    bucket = _gcs().bucket(req.gcs_bucket)
    blob = bucket.blob(req.gcs_blob)
    if not blob.exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    suffix = os.path.splitext(req.gcs_blob)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        blob.download_to_filename(tmp_path)

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="Could not open video")

        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise HTTPException(status_code=400, detail="Could not read first frame")

        h, w = frame.shape[:2]
        results = MODEL(frame, verbose=False, imgsz=req.resolution)
        persons = []
        if results and results[0].keypoints is not None:
            kpts = results[0].keypoints
            boxes = results[0].boxes
            for i in range(len(kpts.xy) if kpts.xy is not None else 0):
                # Bbox
                x1, y1, x2, y2 = 0, 0, 1, 1
                if boxes is not None and len(boxes.xyxy) > i:
                    box = boxes.xyxy[i]
                    x1, y1, x2, y2 = float(box[0]) / w, float(box[1]) / h, float(box[2]) / w, float(box[3]) / h
                area = (x2 - x1) * (y2 - y1)
                if area < 0.02:
                    continue

                # Keypoints
                xy = kpts.xy[i]
                conf = kpts.conf[i] if kpts.conf is not None else None
                person_kpts = {}
                for k in range(len(xy)):
                    vis = float(conf[k]) if conf is not None else 1.0
                    person_kpts[str(k)] = {
                        "x": round(float(xy[k][0]) / w, 5),
                        "y": round(float(xy[k][1]) / h, 5),
                        "visibility": round(vis, 3),
                    }

                persons.append({
                    "x1": round(x1, 4), "y1": round(y1, 4),
                    "x2": round(x2, 4), "y2": round(y2, 4),
                    "center_x": round((x1 + x2) / 2, 4),
                    "area": round(area, 4),
                    "keypoints": person_kpts,
                })
        persons.sort(key=lambda p: abs(p["center_x"] - 0.5))
        return {"persons": persons}
    finally:
        os.unlink(tmp_path)


def _iou(a, b):
    """Compute IoU between two boxes {x1,y1,x2,y2}."""
    ix1 = max(a["x1"], b["x1"])
    iy1 = max(a["y1"], b["y1"])
    ix2 = min(a["x2"], b["x2"])
    iy2 = min(a["y2"], b["y2"])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
    area_b = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0


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
        # After first frame with selected_boxes, compute reference area for filtering
        ref_area = None  # will be set from first matched frame
        # Cap persons per frame to the number of user-selected boxes
        max_persons = len(req.selected_boxes) if req.selected_boxes else None
        # Track ID → role mapping, built from early frames where IoU matching works
        track_to_role = {}

        # Reset YOLO tracker state for this new video
        MODEL.predictor = None

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % req.sample_every_n == 0:
                h, w = frame.shape[:2]
                results = MODEL.track(frame, verbose=False, imgsz=req.resolution, persist=True)

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
                        person_data = {"keypoints": person_kpts, "center_x": round(cx, 4), "bbox_area": round(bbox_area, 4), "track_id": track_id}
                        # Store bbox for IoU matching with selected_boxes
                        if boxes is not None and len(boxes.xyxy) > p_idx:
                            box = boxes.xyxy[p_idx]
                            person_data["bbox"] = {
                                "x1": round(float(box[0]) / w, 4), "y1": round(float(box[1]) / h, 4),
                                "x2": round(float(box[2]) / w, 4), "y2": round(float(box[3]) / h, 4),
                            }
                        persons.append(person_data)

                # Filter out small bboxes (background people/audience)
                if ref_area is not None:
                    # Use relative filtering: keep persons >= 40% of reference area
                    persons = [p for p in persons if p["bbox_area"] > ref_area * 0.4]
                else:
                    persons = [p for p in persons if p["bbox_area"] > 0.02]

                # If user selected specific boxes, only keep persons that overlap
                if req.selected_boxes:
                    for p in persons:
                        if "bbox" not in p:
                            p["_best_iou"] = 0
                            continue
                        best_iou, best_label = 0, None
                        for sb in req.selected_boxes:
                            score = _iou(p["bbox"], sb)
                            if score > best_iou:
                                best_iou = score
                                best_label = sb.get("label")
                        p["_best_iou"] = best_iou
                        if best_label and best_iou > 0.2:
                            p["label"] = best_label

                    matched = [p for p in persons if p["_best_iou"] > 0.2]

                    if matched:
                        persons = matched
                    else:
                        # IoU matching failed (people moved far from frame-1 boxes).
                        # Fall back to the largest N persons by bbox area.
                        persons.sort(key=lambda p: p["bbox_area"], reverse=True)
                        if max_persons:
                            persons = persons[:max_persons]

                    # Record track_id → role from IoU-matched frames
                    for p in persons:
                        tid = p.get("track_id", -1)
                        label = p.get("label")
                        if tid >= 0 and label and tid not in track_to_role:
                            track_to_role[tid] = label

                    # Set reference area from first matched frame
                    if ref_area is None and len(persons) > 0:
                        ref_area = sum(p["bbox_area"] for p in persons) / len(persons)

                # Sort by distance to frame center (closest first)
                persons.sort(key=lambda p: abs(p["center_x"] - 0.5))

                # Enforce max_persons cap (e.g. 2 ticked = at most 2 per frame)
                if max_persons and len(persons) > max_persons:
                    persons = persons[:max_persons]

                # Clean up internal fields before saving
                for p in persons:
                    p.pop("_best_iou", None)

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
            "role_labels": {str(k): v for k, v in track_to_role.items()},
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
