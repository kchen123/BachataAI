"""Pose estimation microservice — GPU-accelerated YOLO26x-Pose inference.

This service is fully self-contained: it processes the video, saves results
to GCS, and writes progress to GCS. The Flask app only needs to trigger it
and poll GCS for status. No background threads needed.
"""

import os
import json
import copy
import tempfile
import torch
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import storage
from ultralytics import YOLO

app = FastAPI(title="BachataAI Pose Service")

MODEL_PATH = "yolo26x-pose.pt"
MODEL_BUCKET = os.environ.get("MODEL_BUCKET", "bachataai-videos")
MODEL_BLOB = os.environ.get("MODEL_BLOB", "models/yolo26x-pose.pt")
CONFIG_BLOB = os.environ.get("CONFIG_BLOB", "config/pose_config.json")
GCS_CLIENT = None


def _gcs():
    global GCS_CLIENT
    if GCS_CLIENT is None:
        GCS_CLIENT = storage.Client()
    return GCS_CLIENT


def _load_config():
    """Load pose config from GCS, fall back to local default."""
    try:
        blob = _gcs().bucket(MODEL_BUCKET).blob(CONFIG_BLOB)
        cfg = json.loads(blob.download_as_bytes())
        print(f"Config loaded from gs://{MODEL_BUCKET}/{CONFIG_BLOB}")
        return cfg
    except Exception as e:
        print(f"Config load failed ({e}), using local default")
        local = os.path.join(os.path.dirname(__file__), "default_config.json")
        if os.path.exists(local):
            with open(local) as f:
                return json.load(f)
        return {}


def _load_model():
    """Load YOLO model, downloading from GCS if not baked into the image."""
    if not os.path.exists(MODEL_PATH):
        print(f"Downloading model from gs://{MODEL_BUCKET}/{MODEL_BLOB}...")
        blob = _gcs().bucket(MODEL_BUCKET).blob(MODEL_BLOB)
        blob.download_to_filename(MODEL_PATH)
        print(f"Model downloaded ({os.path.getsize(MODEL_PATH) // 1024 // 1024}MB)")
    return YOLO(MODEL_PATH)


CFG = _load_config()
MODEL = _load_model()


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
    person_roles: dict | None = None   # {person_index: "lead"|"follow"} from first-frame detection
    max_persons: int | None = None     # cap persons per frame (e.g. 2 for lead+follow)


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
                if area < CFG.get("tracking", {}).get("min_bbox_area", 0.02):
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


def _bbox_center(bbox):
    """Return (cx, cy) of a bbox dict."""
    return ((bbox["x1"] + bbox["x2"]) / 2, (bbox["y1"] + bbox["y2"]) / 2)


def _center_distance(bbox_a, bbox_b):
    """Euclidean distance between bbox centers."""
    ca, cb = _bbox_center(bbox_a), _bbox_center(bbox_b)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def _match_persons_to_prev(prev_persons, curr_persons):
    """Greedy match current detections to previous frame's tracked persons.

    Uses a combined score of IoU and center distance.  Returns a dict mapping
    prev person_id → index in curr_persons (or None if unmatched).
    """
    if not prev_persons or not curr_persons:
        return {}

    n_prev = len(prev_persons)
    n_curr = len(curr_persons)

    # Build cost matrix: higher is better match
    scores = np.zeros((n_prev, n_curr))
    for i, pp in enumerate(prev_persons):
        for j, cp in enumerate(curr_persons):
            if "bbox" in pp and "bbox" in cp:
                iou = _iou(pp["bbox"], cp["bbox"])
                dist = _center_distance(pp["bbox"], cp["bbox"])
                # Combined score: IoU (0-1) + proximity bonus (0-1)
                scores[i, j] = iou + max(0, 1.0 - dist * CFG.get("tracking", {}).get("proximity_weight", 3.0))
            else:
                scores[i, j] = 0

    # Greedy assignment: pick best score, then remove both from pool
    assignments = {}
    used_curr = set()
    for _ in range(min(n_prev, n_curr)):
        best_score = -1
        best_i, best_j = -1, -1
        for i in range(n_prev):
            if i in assignments:
                continue
            for j in range(n_curr):
                if j in used_curr:
                    continue
                if scores[i, j] > best_score:
                    best_score = scores[i, j]
                    best_i, best_j = i, j
        if best_score < CFG.get("tracking", {}).get("match_score_threshold", 0.15):
            break
        assignments[best_i] = best_j
        used_curr.add(best_j)

    return assignments


def _postprocess_tracks(raw_frames, person_roles, max_persons):
    """Post-process raw detections: assign stable person IDs, filter, smooth.

    Args:
        person_roles: {person_index: "lead"|"follow"} — indices match the
                      order returned by /detect-first-frame (sorted by
                      center distance to frame center, small boxes excluded).
    Pipeline:
        1. Identify target dancers in frame 0 by person_roles indices
        2. Track them across all frames using frame-to-frame matching
        3. Filter out everyone else
        4. Smooth keypoints with EMA to reduce jitter
    """
    if not raw_frames:
        return raw_frames

    n_targets = max_persons or (len(person_roles) if person_roles else 2)

    # --- Step 1: Identify targets in first frame ---
    first_persons = raw_frames[0]["persons"]

    if person_roles and first_persons:
        # person_roles keys are indices from /detect-first-frame which filters
        # small boxes (area < 2%) and sorts by center distance.  The raw
        # inference in _run_analysis keeps ALL detections, so we need to
        # replicate that filter+sort to align the indices.
        fp_sorted = sorted(
            [p for p in first_persons if p["bbox_area"] > CFG.get("tracking", {}).get("min_bbox_area", 0.02)],
            key=lambda p: abs(p["center_x"] - 0.5),
        )
        targets = []
        for idx_str, role in person_roles.items():
            idx = int(idx_str)
            if idx < len(fp_sorted):
                fp_sorted[idx]["label"] = role
                targets.append(fp_sorted[idx])

        # If not enough matched, fill with largest remaining
        if len(targets) < n_targets:
            remaining = [p for p in first_persons if p not in targets]
            remaining.sort(key=lambda p: p["bbox_area"], reverse=True)
            targets.extend(remaining[:n_targets - len(targets)])
    else:
        # No roles provided: pick the N largest persons
        first_persons.sort(key=lambda p: p["bbox_area"], reverse=True)
        targets = first_persons[:n_targets]

    # Assign person_id to targets
    for idx, t in enumerate(targets):
        t["person_id"] = idx

    # Compute reference area for size filtering
    ref_area = sum(t["bbox_area"] for t in targets) / len(targets) if targets else 0.05

    # Store tracked persons for frame-to-frame matching
    prev_tracked = list(targets)
    raw_frames[0]["persons"] = list(targets)

    # --- Step 2: Track across all subsequent frames ---
    lost_count = [0] * n_targets  # how many frames each target has been missing

    for fi in range(1, len(raw_frames)):
        frame = raw_frames[fi]
        candidates = frame["persons"]

        # Pre-filter: remove tiny detections (< 30% of reference area)
        candidates = [p for p in candidates if p["bbox_area"] > ref_area * CFG.get("tracking", {}).get("relative_size_filter", 0.3)]

        # Match candidates to previously tracked persons
        assignments = _match_persons_to_prev(prev_tracked, candidates)

        new_tracked = []
        for pid in range(len(prev_tracked)):
            if pid in assignments:
                matched = candidates[assignments[pid]]
                matched["person_id"] = prev_tracked[pid].get("person_id", pid)
                # Carry forward the label from initial assignment
                if "label" in prev_tracked[pid] and "label" not in matched:
                    matched["label"] = prev_tracked[pid]["label"]
                new_tracked.append(matched)
                lost_count[pid] = 0
            else:
                # Person not found — carry forward previous data for a few frames
                lost_count[pid] += 1
                if lost_count[pid] <= CFG.get("tracking", {}).get("ghost_frames", 5):
                    ghost = copy.deepcopy(prev_tracked[pid])
                    ghost["_ghost"] = True
                    new_tracked.append(ghost)
                else:
                    # Lost too long — try to re-acquire from unmatched candidates
                    unmatched = [c for j, c in enumerate(candidates)
                                 if j not in assignments.values()]
                    if unmatched:
                        if "bbox" in prev_tracked[pid]:
                            unmatched.sort(key=lambda c: _center_distance(
                                prev_tracked[pid]["bbox"], c["bbox"]) if "bbox" in c else 999)
                        best = unmatched[0]
                        best["person_id"] = prev_tracked[pid].get("person_id", pid)
                        if "label" in prev_tracked[pid]:
                            best["label"] = prev_tracked[pid]["label"]
                        new_tracked.append(best)
                        lost_count[pid] = 0
                    else:
                        ghost = copy.deepcopy(prev_tracked[pid])
                        ghost["_ghost"] = True
                        new_tracked.append(ghost)

        prev_tracked = new_tracked
        frame["persons"] = new_tracked

    # --- Step 3: Smooth keypoints with EMA ---
    ema_alpha = CFG.get("smoothing", {}).get("ema_alpha", 0.4)
    # Per-person EMA state: person_id → {kpt_index: (x, y)}
    ema_state = {}
    vis_cutoff = CFG.get("smoothing", {}).get("visibility_cutoff", 0.3)

    for frame in raw_frames:
        for person in frame["persons"]:
            pid = person.get("person_id", -1)
            if pid not in ema_state:
                ema_state[pid] = {}

            kpts = person.get("keypoints", {})
            for k, kpt in kpts.items():
                x, y, vis = kpt["x"], kpt["y"], kpt.get("visibility", 0)

                if k in ema_state[pid] and vis > vis_cutoff:
                    prev_x, prev_y = ema_state[pid][k]
                    alpha = ema_alpha * min(vis, 1.0)
                    sx = alpha * x + (1 - alpha) * prev_x
                    sy = alpha * y + (1 - alpha) * prev_y
                    kpt["x"] = round(sx, 5)
                    kpt["y"] = round(sy, 5)
                    ema_state[pid][k] = (sx, sy)
                elif vis > vis_cutoff:
                    ema_state[pid][k] = (x, y)

    # --- Step 4: Clean up internal fields ---
    for frame in raw_frames:
        for p in frame["persons"]:
            p.pop("_ghost", None)

    return raw_frames


def _run_analysis(req: AnalyzeRequest):
    """Process video end-to-end: inference → post-process → save poses to GCS."""
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

        # --- Phase 1: Collect ALL raw detections (no filtering) ---
        raw_frames = []
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

                        person_kpts = {}
                        for i in range(len(xy)):
                            x_px, y_px = float(xy[i][0]), float(xy[i][1])
                            vis = float(conf[i]) if conf is not None else 1.0
                            person_kpts[str(i)] = {
                                "x": round(x_px / w, 5),
                                "y": round(y_px / h, 5),
                                "visibility": round(vis, 3),
                            }

                        person_data = {"keypoints": person_kpts, "center_x": 0.5, "bbox_area": 0}
                        if boxes is not None and len(boxes.xyxy) > p_idx:
                            box = boxes.xyxy[p_idx]
                            bx1, by1 = float(box[0]) / w, float(box[1]) / h
                            bx2, by2 = float(box[2]) / w, float(box[3]) / h
                            person_data["center_x"] = round((bx1 + bx2) / 2, 4)
                            person_data["bbox_area"] = round((bx2 - bx1) * (by2 - by1), 4)
                            person_data["bbox"] = {
                                "x1": round(bx1, 4), "y1": round(by1, 4),
                                "x2": round(bx2, 4), "y2": round(by2, 4),
                            }
                        persons.append(person_data)

                # Use the video container's actual timestamp (ms)
                ts_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
                ts_sec = round(ts_ms / 1000.0, 4) if ts_ms > 0 else round(frame_num / fps, 4)

                raw_frames.append({
                    "frame_num": frame_num,
                    "timestamp_sec": ts_sec,
                    "persons": persons,
                })

                processed += 1

                # Update progress + check cancel every 200 processed frames
                if processed % 200 == 0:
                    pct = 10 + int((processed / max(frames_to_process, 1)) * 70)
                    alive = _write_progress(bucket, req.video_id, {
                        "status": "running", "percent": min(pct, 80),
                        "step": f"Analyzed {processed}/{frames_to_process} frames ({min(pct, 80)}%)"
                    })
                    if not alive:
                        cap.release()
                        _write_progress(bucket, req.video_id, {
                            "status": "cancelled", "percent": 0, "step": "Cancelled"
                        }, force=True)
                        return {"status": "cancelled", "video_id": req.video_id}

            frame_num += 1

        cap.release()

        # Check cancel before post-processing
        alive = _write_progress(bucket, req.video_id, {
            "status": "running", "percent": 82,
            "step": "Tracking dancers and filtering..."
        })
        if not alive:
            _write_progress(bucket, req.video_id, {
                "status": "cancelled", "percent": 0, "step": "Cancelled"
            }, force=True)
            return {"status": "cancelled", "video_id": req.video_id}

        # --- Phase 2: Post-process — track, filter, assign stable IDs, smooth ---
        max_persons = req.max_persons or (len(req.person_roles) if req.person_roles else None)
        raw_frames = _postprocess_tracks(raw_frames, req.person_roles, max_persons)

        _write_progress(bucket, req.video_id, {
            "status": "running", "percent": 90,
            "step": f"Saving {len(raw_frames)} frames to storage..."
        })

        # Build final output (add legacy keypoints field for backwards compat)
        frames = []
        for rf in raw_frames:
            rf["keypoints"] = rf["persons"][0]["keypoints"] if rf["persons"] else {}
            frames.append(rf)

        pose_data = json.dumps({
            "frame_count": frame_num,
            "fps": round(fps, 2),
            "frames": frames,
        })

        pose_blob = bucket.blob(f"poses/{req.video_id}.json")
        pose_blob.upload_from_string(pose_data, content_type="application/json")

        # Write final status — force to ensure frontend knows job ended
        cancel_blob = bucket.blob(f"cancel/{req.video_id}")
        if cancel_blob.exists():
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


# ── Anime conversion ─────────────────────────────────────────────────────────

class AnimifyRequest(BaseModel):
    gcs_bucket: str = "bachataai-videos"
    gcs_blob: str
    job_id: str
    sample_every_n: int = 2
    strength: float = 0.75


def _anime_stylize(frame, strength=0.75):
    """Apply anime-like stylization using OpenCV edge-preserving filters.

    Pipeline:
    1. Bilateral filter to flatten colors while keeping edges
    2. Adaptive threshold to get bold edge lines
    3. Bitwise-AND to combine flat colors with edge mask
    4. Color quantization for cel-shaded look
    5. Blend with original based on strength
    """
    h, w = frame.shape[:2]

    # Downscale for speed if large
    scale = 1.0
    if max(h, w) > 720:
        scale = 720 / max(h, w)
        frame_small = cv2.resize(frame, (int(w * scale), int(h * scale)))
    else:
        frame_small = frame.copy()

    # Step 1: Flatten colors with bilateral filter (multiple passes for stronger effect)
    color = frame_small
    for _ in range(3):
        color = cv2.bilateralFilter(color, 9, 75, 75)

    # Step 2: Edge detection
    gray = cv2.cvtColor(frame_small, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 7)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 9, 2)
    edges_color = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

    # Step 3: Combine flat colors with edges
    cartoon = cv2.bitwise_and(color, edges_color)

    # Step 4: Color quantization for cel-shading
    div = 32
    cartoon = (cartoon // div) * div + div // 2

    # Step 5: Slight saturation boost for anime look
    hsv = cv2.cvtColor(cartoon, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.3, 0, 255)
    cartoon = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # Upscale back if needed
    if scale != 1.0:
        cartoon = cv2.resize(cartoon, (w, h))
        frame_small = frame

    # Step 6: Blend with original based on strength
    result = cv2.addWeighted(cartoon, strength, frame, 1.0 - strength, 0)
    return result


def _run_animify(req: AnimifyRequest):
    """Process video: apply anime style to each frame, encode result, upload to GCS."""
    bucket = _gcs().bucket(req.gcs_bucket)

    _write_progress(bucket, f"anime-{req.job_id}", {
        "status": "running", "percent": 5, "step": "Downloading video..."
    }, force=True)

    # Download video
    blob = bucket.blob(req.gcs_blob)
    if not blob.exists():
        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "error", "percent": 0, "step": f"Video not found: {req.gcs_blob}"
        }, force=True)
        return

    suffix = os.path.splitext(req.gcs_blob)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        blob.download_to_filename(tmp_path)

    out_path = tmp_path + "_anime.mp4"

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            _write_progress(bucket, f"anime-{req.job_id}", {
                "status": "error", "percent": 0, "step": "Could not open video"
            }, force=True)
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames_to_process = total_frames // req.sample_every_n

        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "running", "percent": 10,
            "step": f"Processing {frames_to_process} frames..."
        }, force=True)

        # Output video writer (raw mp4v, will re-encode to H.264 after)
        raw_path = tmp_path + "_raw.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out_fps = fps / req.sample_every_n
        writer = cv2.VideoWriter(raw_path, fourcc, out_fps, (w, h))

        frame_num = 0
        processed = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % req.sample_every_n == 0:
                anime_frame = _anime_stylize(frame, req.strength)
                writer.write(anime_frame)
                processed += 1

                if processed % 50 == 0:
                    pct = 10 + int((processed / max(frames_to_process, 1)) * 80)
                    _write_progress(bucket, f"anime-{req.job_id}", {
                        "status": "running", "percent": min(pct, 90),
                        "step": f"Stylized {processed}/{frames_to_process} frames"
                    }, force=True)

            frame_num += 1

        cap.release()
        writer.release()

        # Re-encode to H.264 for browser playback
        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "running", "percent": 91,
            "step": "Encoding H.264..."
        }, force=True)

        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-i", raw_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            out_path,
        ], capture_output=True, timeout=300)
        os.unlink(raw_path)

        # Upload result
        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "running", "percent": 95,
            "step": "Uploading anime video..."
        }, force=True)

        result_blob_name = f"anime/{req.job_id}.mp4"
        result_blob = bucket.blob(result_blob_name)
        result_blob.upload_from_filename(out_path, content_type="video/mp4")

        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "done", "percent": 100,
            "step": f"Complete — {processed} frames stylized",
            "result_url": result_blob.public_url,
            "result_gcs_path": result_blob_name,
        }, force=True)

    except Exception as e:
        _write_progress(bucket, f"anime-{req.job_id}", {
            "status": "error", "percent": 0, "step": str(e)
        }, force=True)
    finally:
        os.unlink(tmp_path)
        for p in [raw_path, out_path]:
            if os.path.exists(p):
                os.unlink(p)


@app.post("/animify")
def animify_video(req: AnimifyRequest):
    """Apply anime style transfer to a video. Blocks until done.
    Progress written to GCS at progress/anime-{job_id}.json
    Result saved to GCS at anime/{job_id}.mp4
    """
    _run_animify(req)
    return {"status": "done", "job_id": req.job_id}


@app.post("/analyze")
def analyze_video(req: AnalyzeRequest):
    """Runs analysis synchronously — blocks until done.
    Must block so Cloud Run keeps the container alive.
    Progress is written to GCS at gs://bucket/progress/<video_id>.json
    Results saved to gs://bucket/poses/<video_id>.json
    """
    _run_analysis(req)
    return {"status": "done", "video_id": req.video_id}
