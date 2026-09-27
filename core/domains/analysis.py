"""Analysis domain — run YOLO26 pose estimation on a video."""

import math
import cv2
from core.adapters import AnalysesAdapter, JointFramesAdapter, get_repo
from core.domains.videos import video_filepath, get_video


# COCO 17 keypoints from YOLO26-Pose
LANDMARK_NAMES = {
    0: "nose",
    1: "left_eye",
    2: "right_eye",
    3: "left_ear",
    4: "right_ear",
    5: "left_shoulder",
    6: "right_shoulder",
    7: "left_elbow",
    8: "right_elbow",
    9: "left_wrist",
    10: "right_wrist",
    11: "left_hip",
    12: "right_hip",
    13: "left_knee",
    14: "right_knee",
    15: "left_ankle",
    16: "right_ankle",
}

# Joint angle definitions using COCO indices: (point_a, vertex, point_b)
ANGLE_DEFINITIONS = {
    "left_elbow": (5, 7, 9),       # shoulder -> elbow -> wrist
    "right_elbow": (6, 8, 10),
    "left_shoulder": (7, 5, 11),    # elbow -> shoulder -> hip
    "right_shoulder": (8, 6, 12),
    "left_hip": (5, 11, 13),       # shoulder -> hip -> knee
    "right_hip": (6, 12, 14),
    "left_knee": (11, 13, 15),     # hip -> knee -> ankle
    "right_knee": (12, 14, 16),
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


def _get_model():
    """Load YOLO26 pose model (auto-downloads on first use)."""
    from ultralytics import YOLO
    return YOLO("yolo26x-pose.pt")


def run_pose_estimation(video_id: int, on_progress=None):
    """Run YOLO26 pose estimation on every frame of a video."""
    video = get_video(video_id)
    if video is None:
        raise ValueError("Video not found")

    repo = get_repo()
    aa = AnalysesAdapter(repo)
    ja = JointFramesAdapter(repo)

    analysis = aa.create(video_id=video_id, model="yolo26x-pose")
    aa.set_status(analysis["id"], "running")

    filepath = str(video_filepath(video))
    cap = cv2.VideoCapture(filepath)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    batch = []
    all_angles = {name: [] for name in ANGLE_DEFINITIONS}

    model = _get_model()

    try:
        frame_num = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # Run YOLO26 pose on frame
            results = model(frame, verbose=False)
            h, w = frame.shape[:2]

            landmarks = {}
            if results and results[0].keypoints is not None:
                kpts = results[0].keypoints
                if kpts.xy is not None and len(kpts.xy) > 0:
                    # Take the first detected person
                    xy = kpts.xy[0]       # shape: (17, 2)
                    conf = kpts.conf[0] if kpts.conf is not None else None

                    for i in range(len(xy)):
                        x_px, y_px = float(xy[i][0]), float(xy[i][1])
                        vis = float(conf[i]) if conf is not None else 1.0
                        # Normalize to 0-1 like MediaPipe
                        landmarks[i] = {
                            "x": round(x_px / w, 5),
                            "y": round(y_px / h, 5),
                            "z": 0.0,
                            "visibility": round(vis, 3),
                        }

                    # Calculate angles
                    for angle_name, (a, b, c) in ANGLE_DEFINITIONS.items():
                        if a in landmarks and b in landmarks and c in landmarks:
                            if landmarks[a]["visibility"] > 0.3 and landmarks[b]["visibility"] > 0.3 and landmarks[c]["visibility"] > 0.3:
                                angle = _calc_angle(landmarks[a], landmarks[b], landmarks[c])
                                all_angles[angle_name].append(round(angle, 1))

            timestamp_sec = round(frame_num / fps, 4)
            batch.append({
                "frame_num": frame_num,
                "timestamp_sec": timestamp_sec,
                "landmarks": landmarks,
            })

            if len(batch) >= 30:
                ja.bulk_insert(analysis["id"], batch)
                batch = []

            if on_progress:
                on_progress(frame_num, total_frames)

            frame_num += 1
    except Exception:
        aa.set_status(analysis["id"], "error")
        cap.release()
        raise
    finally:
        cap.release()

    if batch:
        ja.bulk_insert(analysis["id"], batch)

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
