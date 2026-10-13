"""Google Cloud Storage adapter for video files."""

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
