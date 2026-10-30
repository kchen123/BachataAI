import os
import glob
import concurrent.futures
from google.cloud import storage

BUCKET = "bachataai-videos"
GCS_FOLDER = "my_videos/bachata/cristian_gabriella"
LOCAL_DIR = os.path.join(os.path.dirname(__file__), "my videos bachata")

client = storage.Client()
bucket = client.bucket(BUCKET)

files = glob.glob(os.path.join(LOCAL_DIR, "*.mp4"))
total = len(files)
print(f"Uploading {total} files to gs://{BUCKET}/{GCS_FOLDER}/", flush=True)

uploaded = 0

def upload(filepath):
    global uploaded
    filename = os.path.basename(filepath)
    blob = bucket.blob(f"{GCS_FOLDER}/{filename}")
    blob.upload_from_filename(filepath)
    uploaded += 1
    print(f"  [{uploaded}/{total}] {filename}", flush=True)

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
    pool.map(upload, files)

print("Done!", flush=True)
