# Compare / Find Similar Moves

## Overview
Find similar dance moves across all analyzed videos using Pearson correlation on pose keypoints.

## Flow
1. Open a video with pose data
2. Click **Compare** button in pose controls
3. Pause at start of move → **Set Start**
4. Pause at end of move → **Set End**
5. Click **Find Similar** → searches all analyzed videos
6. Results ranked by similarity score, click to jump to matching video/time

## Backend API

### `POST /api/videos/<video_id>/compare`

**Request:**
```json
{
  "start_sec": 12.5,
  "end_sec": 15.3
}
```

**Response:**
```json
{
  "results": [
    {
      "video_id": 42,
      "title": "Oliver Pineda & Mercedes",
      "gcs_url": "https://...",
      "has_pose": true,
      "score": 0.87,
      "start_sec": 23.1,
      "end_sec": 25.9
    }
  ]
}
```

## Algorithm
1. Extract keypoints for selected time range from source video's pose JSON
2. Normalize keypoints relative to hip center (midpoint of keypoints 11+12)
3. Flatten each frame's 17 keypoints into a 34-element vector (x,y pairs)
4. Sliding window across each candidate video's pose data
5. Pearson correlation between source sequence and each window
6. Return top 10 matches with correlation > 0.5

## Key Functions

### `_extract_keypoints_array(frames, start_sec, end_sec)`
- Filters frames by time range
- Normalizes x,y to hip center
- Returns numpy array of shape (n_frames, 34)

### `_pearson_sequence(query, target)`
- Slides query window across target
- Computes Pearson correlation at each position
- Returns (best_correlation, best_start_index)

## UI Components
- **Compare button** in pose controls bar (only visible when has_pose=true)
- **Compare bar** with Set Start, Set End, Find Similar, Cancel buttons
- **Results panel** below controls showing ranked matches
- Each result: score %, video id | title, time range — clickable to open video

## Notes
- Only searches videos with has_pose=true
- Minimum 3 frames required in selected range
- Score threshold: 0.5 (50%)
- Performance: loads each candidate's pose JSON from GCS — could be slow with many analyzed videos
- Future: pre-compute embeddings and store in DB for faster search
