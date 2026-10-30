#!/bin/bash
DIR="core/my videos bachata"
BUCKET="gs://bachataai-videos"
FOLDER_ID=19
USER_ID=1
count=0
total=$(ls "$DIR"/*.mp4 | wc -l)

declare -A seen_posts
for f in "$DIR"/*.mp4; do
  title=$(basename "$f" .mp4)
  post_id=$(echo "$title" | cut -d'_' -f1)
  # Skip carousel duplicates (same post ID)
  if [[ -n "${seen_posts[$post_id]}" ]]; then
    echo "SKIP (carousel dupe): $title"
    continue
  fi
  seen_posts[$post_id]=1
  fname="$(python -c "import uuid; print(uuid.uuid4().hex)").mp4"
  gcloud storage cp "$f" "$BUCKET/$fname" --quiet
  if [ $? -eq 0 ]; then
    count=$((count+1))
    python -c "
from core.adapters.repository import Repository
from core.adapters.videos import VideosAdapter
r = Repository()
try:
    VideosAdapter(r).create('$title', $USER_ID, $FOLDER_ID, filename='$fname', gcs_url='https://storage.googleapis.com/bachataai-videos/$fname')
except Exception:
    r.conn.rollback()
r.close()
"
    echo "[$count/$total] $title"
  else
    echo "FAILED: $title"
  fi
done
echo "Done! $count uploaded."
