#!/bin/bash
# Upload one video per post (largest clip) to folder 19
cd "C:/Users/helen/Documents/KEVIN_CHEN/Apps/BachataAI"

# Step 1: Python picks the best file per post, writes list to temp file
python -c "
import os, glob
from collections import defaultdict

files = glob.glob('core/my videos bachata/*.mp4')
groups = defaultdict(list)
for f in files:
    title = os.path.splitext(os.path.basename(f))[0]
    post_id = title.split('_')[0].split('.')[0]
    size = os.path.getsize(f)
    groups[post_id].append((f, title, size))

with open('core/upload_list.txt', 'w') as out:
    for post_id, clips in sorted(groups.items()):
        clips.sort(key=lambda x: x[2], reverse=True)
        out.write(clips[0][0] + '\n')
print(f'{len(groups)} unique posts selected')
"

BUCKET="gs://bachataai-videos"
FOLDER_ID=19
USER_ID=1
GCLOUD="/c/Users/helen/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud"
total=$(wc -l < core/upload_list.txt)
count=0

while IFS= read -r f; do
  fname="$(python -c "import uuid; print(uuid.uuid4().hex)").mp4"
  title=$(basename "$f" .mp4)
  gcs_url="https://storage.googleapis.com/bachataai-videos/$fname"

  "$GCLOUD" storage cp "$f" "$BUCKET/$fname" --quiet 2>/dev/null
  if [ $? -eq 0 ]; then
    python -c "
from core.adapters.repository import Repository
from core.adapters.videos import VideosAdapter
r = Repository()
try:
    VideosAdapter(r).create('$title', $USER_ID, $FOLDER_ID, filename='$fname', gcs_url='$gcs_url')
except: r.conn.rollback()
r.close()
" 2>/dev/null
    count=$((count+1))
    echo "[$count/$total] $title"
  else
    echo "FAILED: $title"
  fi
done < core/upload_list.txt

echo "Done! $count/$total uploaded."
