"""One-shot deduped import. Run from shell: just reads upload_list.txt and calls gcloud."""
import os
import sys
import uuid
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUCKET = "bachataai-videos"

with open("core/upload_list.txt") as f:
    files = [line.strip() for line in f if line.strip()]

total = len(files)
print(f"Uploading {total} files...", flush=True)

count = 0
for i, filepath in enumerate(files, 1):
    fname = f"{uuid.uuid4().hex}.mp4"
    title = os.path.splitext(os.path.basename(filepath))[0]
    gcs_dest = f"gs://{BUCKET}/{fname}"

    result = subprocess.run(
        f'gcloud storage cp "{filepath}" "{gcs_dest}" --quiet',
        shell=True, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"  [{i}/{total}] FAIL upload: {title}", flush=True)
        continue

    from core.adapters.repository import Repository
    from core.adapters.videos import VideosAdapter
    r = Repository()
    try:
        VideosAdapter(r).create(
            title, 1, 19, filename=fname,
            gcs_url=f"https://storage.googleapis.com/{BUCKET}/{fname}",
        )
    except Exception:
        r.conn.rollback()
    r.close()

    count += 1
    print(f"  [{i}/{total}] {title}", flush=True)

print(f"Done! {count}/{total} uploaded.", flush=True)
