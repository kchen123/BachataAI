"""Video domain — upload, list, get, delete."""

import os
import uuid
import cv2
from pathlib import Path
from core.adapters import VideosAdapter, get_repo

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}


def _adapter():
    return VideosAdapter(get_repo())


def upload_video(file_storage) -> dict:
    """Save uploaded file, probe with OpenCV, create DB row."""
    original_name = file_storage.filename
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {ext}")

    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = UPLOAD_DIR / filename
    file_storage.save(str(filepath))

    cap = cv2.VideoCapture(str(filepath))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = frame_count / fps if fps > 0 else 0
    cap.release()

    return _adapter().create(
        filename=filename,
        original_name=original_name,
        duration_sec=round(duration_sec, 2),
        fps=round(fps, 2),
        frame_count=frame_count,
    )


def list_videos() -> list[dict]:
    return _adapter().list_all()


def get_video(video_id: int) -> dict | None:
    return _adapter().get_by_id(video_id)


def delete_video(video_id: int) -> bool:
    video = _adapter().get_by_id(video_id)
    if video is None:
        return False
    filepath = UPLOAD_DIR / video["filename"]
    if filepath.exists():
        os.remove(filepath)
    return _adapter().delete(video_id)


def video_filepath(video: dict) -> Path:
    return UPLOAD_DIR / video["filename"]
