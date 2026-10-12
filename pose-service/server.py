"""Pose estimation microservice — GPU-accelerated YOLO26x-Pose inference."""

import os
import tempfile
import torch
import cv2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import storage
from ultralytics import YOLO

app = FastAPI(title="BachataAI Pose Service")

# Load model once at startup
MODEL = YOLO("yolo26x-pose.pt")
GCS_CLIENT = None


def _gcs_client():
    global GCS_CLIENT
    if GCS_CLIENT is None:
        GCS_CLIENT = storage.Client()
    return GCS_CLIENT


class PredictRequest(BaseModel):
    gcs_bucket: str = "bachataai-videos"
    gcs_blob: str
    sample_every_n: int = 1
    resolution: int = 1280


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gpu": torch.cuda.is_available(),
        "device": str(next(MODEL.model.parameters()).device),
    }


@app.post("/predict/video")
def predict_video(req: PredictRequest):
    # Download video from GCS to temp file
    bucket = _gcs_client().bucket(req.gcs_bucket)
    blob = bucket.blob(req.gcs_blob)
    if not blob.exists():
        raise HTTPException(status_code=404, detail=f"Blob {req.gcs_blob} not found")

    suffix = os.path.splitext(req.gcs_blob)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        blob.download_to_filename(tmp_path)

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="Could not open video file")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frames = []
        frame_num = 0

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
                        # Get bbox center x for left/right tracking
                        cx = 0.5
                        if boxes is not None and len(boxes.xyxy) > p_idx:
                            box = boxes.xyxy[p_idx]
                            cx = float((box[0] + box[2]) / 2) / w

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
                        persons.append({"keypoints": person_kpts, "center_x": round(cx, 4)})

                # Sort by center_x so left person is always index 0
                persons.sort(key=lambda p: p["center_x"])

                frames.append({
                    "frame_num": frame_num,
                    "timestamp_sec": round(frame_num / fps, 4),
                    "persons": persons,
                    # Keep backward compat: first person as "keypoints"
                    "keypoints": persons[0]["keypoints"] if persons else {},
                })

            frame_num += 1

        cap.release()
    finally:
        os.unlink(tmp_path)

    return {
        "frame_count": frame_num,
        "fps": round(fps, 2),
        "frames": frames,
    }
