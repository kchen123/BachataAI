/**
 * Skeleton overlay player — draws MediaPipe landmarks on canvas synced to video.
 */

// MediaPipe Pose connections (pairs of landmark indices)
const POSE_CONNECTIONS = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],  // arms
  [11, 23], [12, 24], [23, 24],                        // torso
  [23, 25], [25, 27], [24, 26], [26, 28],              // legs
  [27, 29], [29, 31], [28, 30], [30, 32],              // feet
  [0, 1], [1, 2], [2, 3], [0, 4], [4, 5], [5, 6],    // face
  [9, 10], [15, 17], [15, 19], [15, 21],               // hands L
  [16, 18], [16, 20], [16, 22],                         // hands R
];

let allFrames = [];
let currentFrameIdx = 0;

document.addEventListener("DOMContentLoaded", async () => {
  const video = document.getElementById("video-player");
  const canvas = document.getElementById("skeleton-canvas");
  const ctx = canvas.getContext("2d");
  const slider = document.getElementById("frame-slider");
  const frameNumEl = document.getElementById("frame-num");
  const frameTimeEl = document.getElementById("frame-time");
  const noAnalysis = document.getElementById("no-analysis");
  const analysisContent = document.getElementById("analysis-content");
  const titleEl = document.getElementById("video-title");
  const metaEl = document.getElementById("video-meta");

  // Load video info
  const vRes = await fetch(`/api/videos/${VIDEO_ID}`);
  if (!vRes.ok) return;
  const videoData = await vRes.json();
  titleEl.textContent = videoData.original_name;
  metaEl.textContent = `${videoData.fps} fps | ${videoData.frame_count} frames | ${Math.round(videoData.duration_sec)}s`;
  document.getElementById("video-source").src = videoData.gcs_url || `/uploads/${videoData.filename}`;
  video.load();

  // Check for analysis
  const aRes = await fetch(`/api/videos/${VIDEO_ID}/analysis`);
  if (!aRes.ok) {
    noAnalysis.classList.remove("hidden");
    setupRunButton(videoData);
    return;
  }

  const analysis = await aRes.json();
  if (analysis.status !== "done") {
    noAnalysis.classList.remove("hidden");
    setupRunButton(videoData);
    return;
  }

  analysisContent.classList.remove("hidden");

  // Load frames (sampled to 500 for performance)
  const fRes = await fetch(`/api/analyses/${analysis.id}/frames?sample=500`);
  allFrames = await fRes.json();

  slider.max = allFrames.length - 1;
  slider.value = 0;

  // Draw skeleton on canvas
  function drawSkeleton(frame) {
    const lm = frame.landmarks || {};
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    // Draw connections
    ctx.strokeStyle = "rgba(124, 92, 252, 0.7)";
    ctx.lineWidth = 2;
    for (const [a, b] of POSE_CONNECTIONS) {
      const pa = lm[a] || lm[String(a)];
      const pb = lm[b] || lm[String(b)];
      if (pa && pb && pa.visibility > 0.3 && pb.visibility > 0.3) {
        ctx.beginPath();
        ctx.moveTo(pa.x * w, pa.y * h);
        ctx.lineTo(pb.x * w, pb.y * h);
        ctx.stroke();
      }
    }

    // Draw joints
    for (const [idx, pt] of Object.entries(lm)) {
      if (pt.visibility > 0.3) {
        ctx.fillStyle = "rgba(192, 132, 252, 0.9)";
        ctx.beginPath();
        ctx.arc(pt.x * w, pt.y * h, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function updateFrame(idx) {
    if (idx < 0 || idx >= allFrames.length) return;
    currentFrameIdx = idx;
    const frame = allFrames[idx];
    drawSkeleton(frame);
    frameNumEl.textContent = `Frame ${frame.frame_num}`;
    frameTimeEl.textContent = `${frame.timestamp_sec.toFixed(2)}s`;
  }

  // Resize canvas to match video
  function resizeCanvas() {
    canvas.width = video.videoWidth || video.clientWidth;
    canvas.height = video.videoHeight || video.clientHeight;
    if (allFrames.length) updateFrame(currentFrameIdx);
  }

  video.addEventListener("loadedmetadata", resizeCanvas);
  window.addEventListener("resize", resizeCanvas);

  // Sync video playback with skeleton
  video.addEventListener("timeupdate", () => {
    if (!allFrames.length) return;
    const t = video.currentTime;
    let closest = 0;
    let minDiff = Infinity;
    for (let i = 0; i < allFrames.length; i++) {
      const diff = Math.abs(allFrames[i].timestamp_sec - t);
      if (diff < minDiff) { minDiff = diff; closest = i; }
    }
    slider.value = closest;
    updateFrame(closest);
  });

  // Manual scrubbing
  slider.addEventListener("input", (e) => {
    const idx = parseInt(e.target.value);
    updateFrame(idx);
    if (allFrames[idx]) {
      video.currentTime = allFrames[idx].timestamp_sec;
    }
  });

  // Build summary
  buildSummary(analysis.summary);

  // Load angles for charts
  if (window.initAngleCharts) {
    window.initAngleCharts(analysis.id);
  }

  // Initial draw
  updateFrame(0);

  function setupRunButton(videoData) {
    const btn = document.getElementById("run-analysis-btn");
    const progressEl = document.getElementById("analysis-progress");
    const statusEl = document.getElementById("analysis-status");

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Starting...";
      progressEl.classList.remove("hidden");

      await fetch(`/api/videos/${VIDEO_ID}/analyze`, { method: "POST" });

      const poll = setInterval(async () => {
        const r = await fetch(`/api/videos/${VIDEO_ID}/analysis`);
        if (r.ok) {
          const a = await r.json();
          if (a.status === "done") {
            clearInterval(poll);
            location.reload();
          } else if (a.status === "error") {
            clearInterval(poll);
            statusEl.textContent = "Error during analysis";
            statusEl.style.color = "var(--danger)";
            btn.disabled = false;
            btn.textContent = "Retry";
          } else {
            statusEl.textContent = "Processing frames...";
          }
        }
      }, 2000);
    });
  }

  function buildSummary(summary) {
    const grid = document.getElementById("summary-grid");
    if (!summary) { grid.innerHTML = "<p>No summary available</p>"; return; }

    const angles = ["left_knee", "right_knee", "left_hip", "right_hip",
                     "left_shoulder", "right_shoulder", "left_elbow", "right_elbow"];

    for (const name of angles) {
      const data = summary[name];
      if (!data) continue;
      const label = name.replace(/_/g, " ");
      grid.innerHTML += `
        <div class="summary-stat">
          <div class="label">${label}</div>
          <div class="value">${data.range}&deg;</div>
          <div class="detail">ROM: ${data.min}&deg; – ${data.max}&deg; | Avg: ${data.avg}&deg;</div>
        </div>`;
    }

    if (summary.total_frames) {
      grid.innerHTML += `
        <div class="summary-stat">
          <div class="label">Total Frames</div>
          <div class="value">${summary.total_frames}</div>
        </div>`;
    }
  }
});
