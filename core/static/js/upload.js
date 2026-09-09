document.addEventListener("DOMContentLoaded", () => {
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const progressBox = document.getElementById("upload-progress");
  const progressFill = document.getElementById("progress-fill");
  const uploadFilename = document.getElementById("upload-filename");
  const uploadPercent = document.getElementById("upload-percent");
  const resultBox = document.getElementById("upload-result");
  const resultDetail = document.getElementById("result-detail");

  // Click to browse
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
  });

  // Drag & drop
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("drag-over");
  });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
  });

  function uploadFile(file) {
    dropZone.classList.add("hidden");
    progressBox.classList.remove("hidden");
    uploadFilename.textContent = file.name;

    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        progressFill.style.width = pct + "%";
        uploadPercent.textContent = pct + "%";
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status === 201) {
        const video = JSON.parse(xhr.responseText);
        progressBox.classList.add("hidden");
        resultBox.classList.remove("hidden");
        resultDetail.textContent =
          `${video.original_name} — ${video.frame_count} frames, ${video.fps} fps, ${Math.round(video.duration_sec)}s`;
      } else {
        uploadPercent.textContent = "Upload failed";
        progressFill.style.background = "var(--danger)";
      }
    });

    xhr.addEventListener("error", () => {
      uploadPercent.textContent = "Upload failed";
      progressFill.style.background = "var(--danger)";
    });

    xhr.open("POST", "/api/videos");
    xhr.send(formData);
  }
});
