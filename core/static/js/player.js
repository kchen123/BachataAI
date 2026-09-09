/**
 * Skeleton overlay player — draws MediaPipe landmarks on canvas synced to video.
 * Reusable: call SkeletonPlayer(elements) to create an instance.
 */

const POSE_CONNECTIONS = [
  [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [24, 26], [26, 28],
  [27, 29], [29, 31], [28, 30], [30, 32],
  [0, 1], [1, 2], [2, 3], [0, 4], [4, 5], [5, 6],
  [9, 10], [15, 17], [15, 19], [15, 21],
  [16, 18], [16, 20], [16, 22],
];

/**
 * Create a skeleton player instance.
 * @param {Object} els - DOM elements
 * @param {HTMLVideoElement} els.video
 * @param {HTMLCanvasElement} els.canvas
 * @param {HTMLInputElement} [els.slider]
 * @param {HTMLElement} [els.frameNumEl]
 * @param {HTMLElement} [els.frameTimeEl]
 * @param {HTMLElement} [els.summaryGrid]
 */
function SkeletonPlayer(els) {
  const { video, canvas, slider, frameNumEl, frameTimeEl, summaryGrid } = els;
  const ctx = canvas.getContext("2d");
  let allFrames = [];
  let currentFrameIdx = 0;

  function drawSkeleton(frame) {
    const lm = frame.landmarks || {};
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

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

    for (const [, pt] of Object.entries(lm)) {
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
    if (frameNumEl) frameNumEl.textContent = `Frame ${frame.frame_num}`;
    if (frameTimeEl) frameTimeEl.textContent = `${frame.timestamp_sec.toFixed(2)}s`;
  }

  function resizeCanvas() {
    canvas.width = video.videoWidth || video.clientWidth;
    canvas.height = video.videoHeight || video.clientHeight;
    if (allFrames.length) updateFrame(currentFrameIdx);
  }

  function onTimeUpdate() {
    if (!allFrames.length) return;
    const t = video.currentTime;
    let closest = 0;
    let minDiff = Infinity;
    for (let i = 0; i < allFrames.length; i++) {
      const diff = Math.abs(allFrames[i].timestamp_sec - t);
      if (diff < minDiff) { minDiff = diff; closest = i; }
    }
    if (slider) slider.value = closest;
    updateFrame(closest);
  }

  function onSliderInput(e) {
    const idx = parseInt(e.target.value);
    updateFrame(idx);
    if (allFrames[idx]) video.currentTime = allFrames[idx].timestamp_sec;
  }

  video.addEventListener("loadedmetadata", resizeCanvas);
  video.addEventListener("timeupdate", onTimeUpdate);
  window.addEventListener("resize", resizeCanvas);
  if (slider) slider.addEventListener("input", onSliderInput);

  function buildSummary(summary) {
    if (!summaryGrid) return;
    summaryGrid.innerHTML = "";
    if (!summary) { summaryGrid.innerHTML = "<p>No summary available</p>"; return; }

    const angles = ["left_knee", "right_knee", "left_hip", "right_hip",
                     "left_shoulder", "right_shoulder", "left_elbow", "right_elbow"];

    for (const name of angles) {
      const data = summary[name];
      if (!data) continue;
      const label = name.replace(/_/g, " ");
      summaryGrid.innerHTML += `
        <div class="summary-stat">
          <div class="label">${label}</div>
          <div class="value">${data.range}&deg;</div>
          <div class="detail">ROM: ${data.min}&deg; – ${data.max}&deg; | Avg: ${data.avg}&deg;</div>
        </div>`;
    }

    if (summary.total_frames) {
      summaryGrid.innerHTML += `
        <div class="summary-stat">
          <div class="label">Total Frames</div>
          <div class="value">${summary.total_frames}</div>
        </div>`;
    }
  }

  return {
    async loadAnalysis(analysisId, summary) {
      const fRes = await fetch(`/api/analyses/${analysisId}/frames?sample=500`);
      allFrames = await fRes.json();
      if (slider) {
        slider.max = allFrames.length - 1;
        slider.value = 0;
      }
      if (summary) buildSummary(summary);
      requestAnimationFrame(() => {
        resizeCanvas();
        updateFrame(0);
      });
    },
    destroy() {
      allFrames = [];
      currentFrameIdx = 0;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      video.removeEventListener("loadedmetadata", resizeCanvas);
      video.removeEventListener("timeupdate", onTimeUpdate);
      window.removeEventListener("resize", resizeCanvas);
      if (slider) slider.removeEventListener("input", onSliderInput);
    },
  };
}
