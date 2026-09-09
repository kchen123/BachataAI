/**
 * Joint angle time-series charts using Chart.js.
 * Reusable: call AngleChartController(canvasEl, selectorEl) to create an instance.
 */

const ANGLE_COLORS = {
  left_knee: "#7c5cfc",
  right_knee: "#c084fc",
  left_hip: "#34d399",
  right_hip: "#6ee7b7",
  left_shoulder: "#fbbf24",
  right_shoulder: "#fcd34d",
  left_elbow: "#f87171",
  right_elbow: "#fca5a5",
};

function AngleChartController(canvasEl, selectorEl) {
  let chart = null;
  let angleData = {};
  let activeAngles = new Set(["left_knee", "right_knee"]);

  function renderChart() {
    const ctx = canvasEl.getContext("2d");
    const datasets = [];
    for (const name of activeAngles) {
      const points = angleData[name] || [];
      datasets.push({
        label: name.replace(/_/g, " "),
        data: points.map((p) => ({ x: p.time, y: p.angle })),
        borderColor: ANGLE_COLORS[name] || "#7c5cfc",
        backgroundColor: "transparent",
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
      });
    }

    if (chart) chart.destroy();

    chart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: {
            type: "linear",
            title: { display: true, text: "Time (s)", color: "#9898aa" },
            ticks: { color: "#6b6b7d" },
            grid: { color: "rgba(51, 51, 70, 0.5)" },
          },
          y: {
            title: { display: true, text: "Angle (deg)", color: "#9898aa" },
            ticks: { color: "#6b6b7d" },
            grid: { color: "rgba(51, 51, 70, 0.5)" },
            min: 0,
            max: 200,
          },
        },
        plugins: {
          legend: {
            labels: { color: "#e8e8f0", usePointStyle: true, pointStyle: "circle" },
          },
          tooltip: {
            backgroundColor: "#22222e",
            titleColor: "#e8e8f0",
            bodyColor: "#9898aa",
            borderColor: "#333346",
            borderWidth: 1,
          },
        },
      },
    });
  }

  return {
    async loadAngles(analysisId) {
      const res = await fetch(`/api/analyses/${analysisId}/angles`);
      angleData = await res.json();

      selectorEl.innerHTML = "";
      for (const name of Object.keys(angleData)) {
        const btn = document.createElement("button");
        btn.textContent = name.replace(/_/g, " ");
        btn.dataset.angle = name;
        if (activeAngles.has(name)) btn.classList.add("active");

        btn.addEventListener("click", () => {
          if (activeAngles.has(name)) {
            activeAngles.delete(name);
            btn.classList.remove("active");
          } else {
            activeAngles.add(name);
            btn.classList.add("active");
          }
          renderChart();
        });

        selectorEl.appendChild(btn);
      }

      renderChart();
    },
    destroy() {
      if (chart) { chart.destroy(); chart = null; }
      angleData = {};
      selectorEl.innerHTML = "";
    },
  };
}
