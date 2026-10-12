# Pose Analysis Pipeline

## Overview

BachataAI uses YOLO26x-Pose on a GPU (NVIDIA L4) to detect human pose keypoints in dance videos. The system tracks multiple dancers per frame with 17 COCO body keypoints each.

## Architecture

```
Browser → Flask App (Cloud Run CPU) → GPU Pose Service (Cloud Run L4)
                                              ↓
                                        GCS (progress + results)
```

- **Flask App**: Triggers analysis, polls GCS for progress, serves pose data to frontend
- **GPU Service**: Downloads video from GCS, runs YOLO inference, writes progress + results to GCS
- **GCS Bucket** (`bachataai-videos`):
  - `poses/<video_id>.json` — full keypoint data
  - `progress/<video_id>.json` — real-time progress updates

## Flow

1. User clicks video → toggles Skeleton → clicks "Run Analysis"
2. Flask creates analysis record in DB (status: `running`)
3. Flask sends `POST /analyze` to GPU service with video info
4. GPU service (blocking request — keeps container alive):
   - Downloads video from GCS
   - Runs YOLO26x-Pose on every Nth frame at specified resolution
   - Writes progress to `gs://bachataai-videos/progress/<video_id>.json` every 200 frames
   - Saves full results to `gs://bachataai-videos/poses/<video_id>.json`
   - Writes final status to progress file
5. Flask polls `progress/<video_id>.json` from GCS every 5 seconds
6. When GPU writes `"status": "done"`, Flask updates DB with summary
7. User clicks video → toggles Skeleton → pose data loaded via `/api/videos/<id>/pose-data`

## Configuration

| Setting | Default | Options |
|---------|---------|---------|
| Sample rate | Every 2nd frame | 1, 2, 5, 10 |
| Resolution | 640px | 320, 480, 640, 1280 |

Configured in `runAnalysis()` in `videos.html` and backend defaults in `analysis.py`.

## Multi-Person Tracking

- YOLO detects all persons in each frame
- Persons sorted by horizontal position (left-to-right) for consistent tracking
- Frontend draws different colored skeletons:
  - Green: left person
  - Blue: right person
  - Purple/Yellow: additional persons

## Data Format

### Progress File (`progress/<video_id>.json`)
```json
{
  "status": "running",
  "percent": 45,
  "step": "Analyzed 6000/13660 frames (45%)"
}
```

### Pose File (`poses/<video_id>.json`)
```json
{
  "frame_count": 27321,
  "fps": 60.0,
  "frames": [
    {
      "frame_num": 0,
      "timestamp_sec": 0.0,
      "persons": [
        {
          "keypoints": {
            "0": {"x": 0.456, "y": 0.263, "z": 0.0, "visibility": 0.988},
            ...
          },
          "center_x": 0.35
        },
        {
          "keypoints": { ... },
          "center_x": 0.65
        }
      ],
      "keypoints": { ... }
    }
  ]
}
```

### COCO 17 Keypoints
```
0: nose          5: left_shoulder   11: left_hip     
1: left_eye      6: right_shoulder  12: right_hip    
2: right_eye     7: left_elbow      13: left_knee    
3: left_ear      8: right_elbow     14: right_knee   
4: right_ear     9: left_wrist      15: left_ankle   
                10: right_wrist     16: right_ankle   
```

## Job Management

### API Endpoints
- `POST /api/videos/<id>/analyze` — start analysis
- `DELETE /api/videos/<id>/analyze` — cancel (UI only, GPU continues)
- `GET /api/videos/<id>/progress` — poll progress
- `GET /api/videos/<id>/analysis` — get analysis metadata
- `GET /api/videos/<id>/pose-data` — proxy pose data from GCS
- `GET /api/admin/jobs` — list all jobs
- `DELETE /api/admin/jobs/<id>` — delete a job + GCS files
- `POST /api/admin/jobs/cleanup` — mark stuck jobs as error

### Jobs Panel
Floating button (bottom-right corner) opens a popup showing all jobs with status, progress, and delete controls.

## Cost

| Component | Cost |
|-----------|------|
| GPU (L4) per video (640px, every 2nd frame) | ~$0.15-0.25 |
| GPU (L4) per video (1280px, every frame) | ~$1.00-1.50 |
| GCS storage | ~$0.02/GB/month |
| GPU idle | $0 (scales to zero) |

## Known Limitations

- Cannot cancel a running GPU job mid-processing (GPU blocks until done)
- Large pose files (>50MB) may be slow to load in browser
- No temporal smoothing on keypoints (raw YOLO output)
- Person tracking is position-based (left/right), not appearance-based — may swap if dancers cross
