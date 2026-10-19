"""One-off: regenerate thumbnails for all videos (makes them public)."""
from core.adapters.repository import Repository
from core.adapters.storage import generate_thumbnail

repo = Repository()
videos = repo.select("bachata.videos")
count = 0
for v in videos:
    if not v.get("gcs_url"):
        continue
    print(f"  #{v['id']} {v['title']}...", end=" ", flush=True)
    url = generate_thumbnail(v["gcs_url"])
    if url:
        repo.update("bachata.videos", v["id"], {"thumbnail_url": url})
        count += 1
        print("OK")
    else:
        print("FAIL")
print(f"Done: {count} thumbnails generated")
repo.close()
