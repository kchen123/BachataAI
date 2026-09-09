"""Video domain — upload, list, get, delete."""

import os
import uuid
import tempfile
import cv2
from pathlib import Path
from core.adapters import VideosAdapter, get_repo
from core.adapters.storage import upload_blob, delete_blob, download_blob

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}


def _adapter():
    return VideosAdapter(get_repo())


def upload_video(file_storage) -> dict:
    """Save uploaded file to GCS, probe with OpenCV, create DB row."""
    original_name = file_storage.filename
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported format: {ext}")

    filename = f"{uuid.uuid4().hex}{ext}"

    # Save to temp file for probing, then upload to GCS
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
        file_storage.save(tmp_path)

    try:
        cap = cv2.VideoCapture(tmp_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_sec = frame_count / fps if fps > 0 else 0
        cap.release()

        gcs_url = upload_blob(filename, tmp_path, CONTENT_TYPES.get(ext, "video/mp4"))
    finally:
        os.unlink(tmp_path)

    return _adapter().create(
        filename=filename,
        original_name=original_name,
        duration_sec=round(duration_sec, 2),
        fps=round(fps, 2),
        frame_count=frame_count,
        gcs_url=gcs_url,
    )


def list_videos() -> list[dict]:
    return _adapter().list_all()


def get_video(video_id: int) -> dict | None:
    return _adapter().get_by_id(video_id)


def delete_video(video_id: int) -> bool:
    video = _adapter().get_by_id(video_id)
    if video is None:
        return False
    delete_blob(video["filename"])
    return _adapter().delete(video_id)


def video_filepath(video: dict) -> Path:
    """Get local path for a video, downloading from GCS if needed."""
    local_path = UPLOAD_DIR / video["filename"]
    if not local_path.exists():
        download_blob(video["filename"], str(local_path))
    return local_path
