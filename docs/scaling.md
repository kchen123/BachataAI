# BachataAI Scaling & Pricing Analysis

## Scenario: 1,000 Users, 80 Minutes Each

- **Total processing:** 80,000 minutes/month
- **Video specs:** 60fps, every 2nd frame, 640px resolution
- **GPU processing rate:** ~3,000 frames/min on NVIDIA L4

---

## Option A: Pay-Per-Use (Cloud Run with GPU)

Current architecture. Scales to zero when idle. No ops overhead.

| Component | Specs | Monthly Cost |
|-----------|-------|-------------|
| GPU (L4) | Auto-scale, max 10 instances | ~$6,000 |
| Flask App | 4 CPU, 4GB, max 50 instances | ~$100 |
| Cloud SQL | 4 CPU, 16GB RAM | ~$200 |
| GCS Storage | ~500GB | ~$15 |
| GCS Egress | ~2TB | ~$50 |
| **Total** | | **~$6,365** |
| **Per user** | | **~$6.37** |

**Pros:** No idle cost, no ops, auto-scales  
**Cons:** Most expensive per-minute, cold starts (30-60s)

---

## Option B: Dedicated GPU VMs (Compute Engine)

Reserved L4 GPU VMs running 24/7. Requires job queue (Redis/Pub-Sub).

| Component | Specs | Monthly Cost |
|-----------|-------|-------------|
| GPU VMs | 3× L4 (g2-standard-8), 24/7 | ~$1,200 |
| Flask App | Cloud Run, same as Option A | ~$100 |
| Cloud SQL | 4 CPU, 16GB RAM | ~$200 |
| GCS Storage + Egress | ~500GB + 2TB | ~$65 |
| Redis (job queue) | Basic tier | ~$35 |
| **Total** | | **~$1,600** |
| **Per user** | | **~$1.60** |

**Pros:** 75% cheaper than pay-per-use  
**Cons:** Paying 24/7 even when idle, need to manage VMs and job queue, no auto-scale

---

## Option C: Spot/Preemptible GPU VMs

Same as Option B but using spot instances (can be interrupted).

| Component | Specs | Monthly Cost |
|-----------|-------|-------------|
| GPU VMs | 3× L4 spot instances | ~$400 |
| Flask App + SQL + GCS + Redis | Same as B | ~$400 |
| **Total** | | **~$800** |
| **Per user** | | **~$0.80** |

**Pros:** Cheapest option  
**Cons:** VMs can be killed anytime (need retry logic), less reliable

---

## Comparison Summary

| Option | Monthly Cost | Per User | Reliability | Ops Effort |
|--------|-------------|----------|-------------|------------|
| A. Cloud Run (pay-per-use) | $6,365 | $6.37 | High | None |
| B. Dedicated VMs | $1,600 | $1.60 | High | Medium |
| C. Spot VMs | $800 | $0.80 | Medium | High |

---

## Suggested Pricing

| Tier | Price | Minutes | Margin (Option A) | Margin (Option B) |
|------|-------|---------|-------------------|-------------------|
| Free | $0 | 10 min/month | Loss leader | Loss leader |
| Basic | $10/month | 80 min | 36% | 84% |
| Pro | $25/month | 250 min | 50% | 90% |
| Unlimited | $50/month | Unlimited* | Depends on usage | 85%+ |

*Unlimited with fair use policy (e.g., max 1,000 min/month)

---

## Scaling Breakpoints

| Users | Best Option | Why |
|-------|-------------|-----|
| 1-50 | A (Cloud Run) | Low volume, scales to zero |
| 50-500 | A or B | B becomes cheaper around 200 users |
| 500-2,000 | B (Dedicated VMs) | Significant cost savings |
| 2,000+ | C (Spot) + B (fallback) | Mix spot for bulk, dedicated for reliability |

---

## Infrastructure Specs at Scale

| Users | Flask | GPU | Cloud SQL | GCS |
|-------|-------|-----|-----------|-----|
| 100 | 2 CPU, 2GB | 1 L4 | db-f1-micro | 50GB |
| 500 | 4 CPU, 4GB, max 20 | 2 L4 | 2 CPU, 8GB | 250GB |
| 1,000 | 4 CPU, 4GB, max 50 | 3 L4 | 4 CPU, 16GB | 500GB |
| 5,000 | 8 CPU, 8GB, max 100 | 10 L4 | 8 CPU, 32GB | 2.5TB |
