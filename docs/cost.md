# BachataAI Cost Breakdown

## GPU Pose Analysis (640px, every 2nd frame)

### Per-Video Cost

| Video Length | 30fps | 60fps |
|-------------|-------|-------|
| 1 min | ~$0.03 | ~$0.05 |
| 3 min | ~$0.08 | ~$0.15 |
| 5 min | ~$0.13 | ~$0.25 |
| 8 min | ~$0.20 | ~$0.40 |
| 10 min | ~$0.25 | ~$0.50 |

### How It's Calculated

- GPU: NVIDIA L4 on Cloud Run @ ~$0.000828/sec (~$3/hr)
- Processing speed: ~3,000 frames/min at 640px
- Every 2nd frame: halves the frame count
- A 5-min 60fps video = 18,000 total frames ÷ 2 = 9,000 to process = ~3 min GPU time

### Monthly Estimates

| Usage | Cost |
|-------|------|
| 10 videos/month (5 min avg, 60fps) | ~$2.50 |
| 50 videos/month | ~$12.50 |
| 100 videos/month | ~$25.00 |
| Idle (no analysis) | $0 (scales to zero) |

## Other Costs

| Service | Monthly Cost |
|---------|-------------|
| Cloud Run (Flask app) | ~$5-10 |
| Cloud SQL (PostgreSQL) | ~$7-10 |
| GCS Storage (per GB) | $0.02/GB |
| GCS Storage (current ~3GB) | ~$0.06 |
| **Total fixed** | **~$12-20** |

## Resolution & Sampling Comparison

| Setting | Speed | Cost | Accuracy |
|---------|-------|------|----------|
| Every frame, 1280px | 1x (slowest) | ~$0.50/min | Best |
| Every 2nd frame, 1280px | 2x | ~$0.25/min | Very good |
| **Every 2nd frame, 640px** | **5x** | **~$0.10/min** | **Good (default)** |
| Every 5th frame, 640px | 10x | ~$0.04/min | Acceptable |
| Every 10th frame, 640px | 20x | ~$0.02/min | Basic |

## Cold Start

- First request after idle: ~30-60s (GPU provisioning + model load)
- Subsequent requests: instant (while container is warm)
- Container stays warm ~5-10 min after last request
