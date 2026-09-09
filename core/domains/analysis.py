"""Analysis domain — run MediaPipe pose estimation on a video."""

import math
import cv2
import urllib.request
from pathlib import Path
from core.adapters import AnalysesAdapter, JointFramesAdapter, get_repo
from core.domains.videos import video_filepath, get_video

# MediaPipe Tasks API (1.0+)
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# Model will be auto-downloaded on first use
_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
_MODEL_PATH = _MODEL_DIR / "pose_landmarker_lite.task"
_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"


def _ensure_model():
    """Download the pose landmarker model if not present."""
    if _MODEL_PATH.exists():
        return str(_MODEL_PATH)
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading pose model to {_MODEL_PATH}...")
    urllib.request.urlretrieve(_MODEL_URL, str(_MODEL_PATH))
    return str(_MODEL_PATH)


# MediaPipe landmark indices for key joints
LANDMARK_NAMES = {i: name for i, name in enumerate([
    "nose", "left_eye_inner", "left_eye", "left_eye_outer",
    "right_eye_inner", "right_eye", "right_eye_outer",
    "left_ear", "right_ear", "mouth_left", "mouth_right",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_pinky", "right_pinky",
    "left_index", "right_index", "left_thumb", "right_thumb",
    "left_hip", "right_hip", "left_knee", "right_knee",
    "left_ankle", "right_ankle", "left_heel", "right_heel",
    "left_foot_index", "right_foot_index",
])}

# Joint angle definitions: (point_a, vertex, point_b)
ANGLE_DEFINITIONS = {
    "left_elbow": (11, 13, 15),    # shoulder -> elbow -> wrist
    "right_elbow": (12, 14, 16),
    "left_shoulder": (13, 11, 23),  # elbow -> shoulder -> hip
    "right_shoulder": (14, 12, 24),
    "left_hip": (11, 23, 25),      # shoulder -> hip -> knee
    "right_hip": (12, 24, 26),
    "left_knee": (23, 25, 27),     # hip -> knee -> ankle
    "right_knee": (24, 26, 28),
}


def _calc_angle(a, b, c) -> float:
    """Angle at vertex b in degrees, given 3 landmarks with x, y."""
    ba = (a["x"] - b["x"], a["y"] - b["y"])
    bc = (c["x"] - b["x"], c["y"] - b["y"])
    dot = ba[0] * bc[0] + ba[1] * bc[1]
    mag_ba = math.sqrt(ba[0]**2 + ba[1]**2)
    mag_bc = math.sqrt(bc[0]**2 + bc[1]**2)
    if mag_ba * mag_bc == 0:
        return 0.0
    cos_angle = max(-1, min(1, dot / (mag_ba * mag_bc)))
    return math.degrees(math.acos(cos_angle))


def run_pose_estimation(video_id: int, on_progress=None):
    """Run MediaPipe on every frame of a video. Returns analysis dict.

    on_progress(frame_num, total_frames) is called per frame for SSE.
    """
    video = get_video(video_id)
    if video is None:
        raise ValueError("Video not found")

    repo = get_repo()
    aa = AnalysesAdapter(repo)
    ja = JointFramesAdapter(repo)

    analysis = aa.create(video_id=video_id)
    aa.set_status(analysis["id"], "running")

    filepath = str(video_filepath(video))
    cap = cv2.VideoCapture(filepath)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    batch = []
    all_angles = {name: [] for name in ANGLE_DEFINITIONS}

    # Set up MediaPipe Tasks PoseLandmarker
    model_path = _ensure_model()
    base_options = mp_python.BaseOptions(model_asset_path=model_path)
    options = vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)

    try:
        frame_num = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int(frame_num * 1000 / fps)
            results = landmarker.detect_for_video(mp_image, timestamp_ms)

            landmarks = {}
            if results.pose_landmarks and len(results.pose_landmarks) > 0:
                pose_lms = results.pose_landmarks[0]
                for i, lm in enumerate(pose_lms):
                    landmarks[i] = {
                        "x": round(lm.x, 5),
                        "y": round(lm.y, 5),
                        "z": round(lm.z, 5),
                        "visibility": round(lm.visibility, 3),
                    }

                # Calculate angles for this frame
                for angle_name, (a, b, c) in ANGLE_DEFINITIONS.items():
                    if a in landmarks and b in landmarks and c in landmarks:
                        angle = _calc_angle(landmarks[a], landmarks[b], landmarks[c])
                        all_angles[angle_name].append(round(angle, 1))

            timestamp_sec = round(frame_num / fps, 4)
            batch.append({
                "frame_num": frame_num,
                "timestamp_sec": timestamp_sec,
                "landmarks": landmarks,
            })

            # Flush in batches of 30
            if len(batch) >= 30:
                ja.bulk_insert(analysis["id"], batch)
                batch = []

            if on_progress:
                on_progress(frame_num, total_frames)

            frame_num += 1
    finally:
        landmarker.close()
        cap.release()

    # Flush remaining
    if batch:
        ja.bulk_insert(analysis["id"], batch)

    # Build summary
    summary = {}
    for angle_name, values in all_angles.items():
        if values:
            summary[angle_name] = {
                "min": min(values),
                "max": max(values),
                "avg": round(sum(values) / len(values), 1),
                "range": round(max(values) - min(values), 1),
            }
    summary["total_frames"] = frame_num
    summary["frames_with_pose"] = frame_num

    aa.set_summary(analysis["id"], summary)
    return aa.get(analysis["id"])


def get_analysis(analysis_id: int) -> dict | None:
    return AnalysesAdapter(get_repo()).get(analysis_id)


def get_latest_analysis(video_id: int) -> dict | None:
    return AnalysesAdapter(get_repo()).get_by_video(video_id)


def get_joint_frames(analysis_id: int) -> list[dict]:
    return JointFramesAdapter(get_repo()).get_by_analysis(analysis_id)


def get_frame_landmarks(analysis_id: int, frame_num: int) -> dict | None:
    return JointFramesAdapter(get_repo()).get_frame(analysis_id, frame_num)
