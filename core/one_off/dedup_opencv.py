import cv2, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.adapters.repository import Repository

repo = Repository()
rows = repo.execute('''
    SELECT id, title FROM bachata.videos
    WHERE folder_id = 19 AND user_id = 1
    ORDER BY title
''', fetch='all')

files_dir = os.path.join(os.path.dirname(__file__), 'my videos bachata')
updated = 0
for r in rows:
    fpath = os.path.join(files_dir, r[1] + '.mp4')
    if not os.path.exists(fpath):
        continue
    cap = cv2.VideoCapture(fpath)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = round(frames / fps, 2) if fps > 0 else 0
    cap.release()
    repo.execute(
        'UPDATE bachata.videos SET duration_sec = %s, fps = %s, frame_count = %s WHERE id = %s',
        [dur, round(fps, 2), frames, r[0]]
    )
    updated += 1

print(f"Updated metadata for {updated} videos", flush=True)

# Find dupes by duration
rows2 = repo.execute('''
    SELECT id, title, duration_sec FROM bachata.videos
    WHERE folder_id = 19 AND user_id = 1 AND duration_sec IS NOT NULL
    ORDER BY duration_sec, title
''', fetch='all')

dur_groups = defaultdict(list)
for r in rows2:
    dur_groups[r[2]].append((r[0], r[1]))

to_delete = []
print('\n=== SAME DURATION (potential dupes) ===', flush=True)
for dur, vids in sorted(dur_groups.items()):
    if len(vids) > 1:
        print(f'  {dur}s: {len(vids)} videos')
        for v in vids:
            print(f'    id={v[0]} {v[1][:60]}')
        for v in vids[1:]:
            to_delete.append(v[0])

print(f'\nDupes to delete: {len(to_delete)}', flush=True)

if to_delete:
    placeholders = ','.join(['%s'] * len(to_delete))
    repo.execute(f'DELETE FROM bachata.videos WHERE id IN ({placeholders})', to_delete)
    print('Deleted!')

remaining = repo.execute('SELECT count(*) FROM bachata.videos WHERE folder_id = 19', fetch='one')
print(f'Remaining: {remaining[0]} videos', flush=True)
repo.close()
