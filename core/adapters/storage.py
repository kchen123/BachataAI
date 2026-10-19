"""Google Cloud Storage adapter for video files."""

import os
import tempfile
import cv2
from google.cloud import storage

BUCKET_NAME = "bachataai-videos"
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def upload_blob(filename: str, local_path: str, content_type: str = "video/mp4"):
    """Upload a file to GCS and return the public URL."""
    bucket = _get_client().bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_filename(local_path, content_type=content_type)
    return blob.public_url


def generate_thumbnail(gcs_url: str, width: int = 320) -> str | None:
    """Download video from GCS, extract frame at 1s, upload JPG thumbnail.
    Returns the public URL of the thumbnail, or None on failure."""
    # Derive blob name from URL
    prefix = f"https://storage.googleapis.com/{BUCKET_NAME}/"
    if not gcs_url.startswith(prefix):
        return None
    blob_name = gcs_url[len(prefix):]
    thumb_name = f"thumbs/{os.path.splitext(blob_name)[0]}.jpg"

    bucket = _get_client().bucket(BUCKET_NAME)

    thumb_blob = bucket.blob(thumb_name)
    # If it already exists, just ensure it's public
    if thumb_blob.exists():
        thumb_blob.make_public()
        return thumb_blob.public_url

    # Download video to temp file
    video_blob = bucket.blob(blob_name)
    if not video_blob.exists():
        return None

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        video_blob.download_to_filename(tmp_path)
        cap = cv2.VideoCapture(tmp_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        # Seek to 1 second
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps))
        ret, frame = cap.read()
        if not ret:
            # Try first frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()
        if not ret:
            return None

        # Resize
        h, w = frame.shape[:2]
        scale = width / w
        frame = cv2.resize(frame, (width, int(h * scale)))

        # Save as JPG
        thumb_path = tmp_path + ".jpg"
        cv2.imwrite(thumb_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

        # Upload and make public
        thumb_blob.upload_from_filename(thumb_path, content_type="image/jpeg")
        thumb_blob.make_public()
        os.unlink(thumb_path)
        return thumb_blob.public_url
    except Exception:
        return None
    finally:
        os.unlink(tmp_path)
