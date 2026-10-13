"""Bulk import local mp4s into BachataAI: upload to GCS + register in DB.

Usage:
    python -m core.bulk_import <local_dir> <folder_id> [--user-id USER_ID]

Example:
    python -m core.bulk_import "downloads/instagram/cristian_gabriella" 19
"""

import os
import sys
import uuid
import glob
import subprocess
import argparse
from core.adapters.repository import Repository
from core.adapters.videos import VideosAdapter

BUCKET = "bachataai-videos"
GCLOUD = r"C:\Users\helen\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"


def bulk_import(local_dir, folder_id, user_id=1):
    # Skip already-uploaded videos
    repo = Repository()
    va = VideosAdapter(repo)
    existing = va.list_by_folder(user_id, folder_id)
    existing_titles = {v["title"] for v in existing}
    repo.close()

    files = glob.glob(os.path.join(local_dir, "*.mp4"))
    to_upload = [f for f in files if os.path.splitext(os.path.basename(f))[0] not in existing_titles]
    total = len(to_upload)
    skipped = len(files) - total
    print(f"Total: {len(files)}, Skipping: {skipped}, To upload: {total}", flush=True)

    for i, filepath in enumerate(to_upload, 1):
        fname = f"{uuid.uuid4().hex}.mp4"
        title = os.path.splitext(os.path.basename(filepath))[0]
        gcs_path = f"gs://{BUCKET}/{fname}"
        gcs_url = f"https://storage.googleapis.com/{BUCKET}/{fname}"

        result = subprocess.run(
            [GCLOUD, "storage", "cp", filepath, gcs_path, "--quiet"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"  [{i}/{total}] FAIL: {title} - {result.stderr}", flush=True)
            continue

        r = Repository()
        try:
            VideosAdapter(r).create(title, user_id, folder_id, filename=fname, gcs_url=gcs_url)
        except Exception:
            r.conn.rollback()
        r.close()
        print(f"  [{i}/{total}] {title}", flush=True)

    print("Done!", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk import mp4s into BachataAI")
    parser.add_argument("local_dir", help="Directory containing mp4 files")
    parser.add_argument("folder_id", type=int, help="Target folder ID in the app")
    parser.add_argument("--user-id", type=int, default=1, help="User ID (default: 1)")
    args = parser.parse_args()

    if not os.path.isdir(args.local_dir):
        print(f"Error: {args.local_dir} is not a directory")
        sys.exit(1)

    bulk_import(args.local_dir, args.folder_id, args.user_id)
