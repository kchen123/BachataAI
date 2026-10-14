FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

# System deps for OpenCV + Python 3.12
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 \
    libgl1-mesa-glx libprotobuf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Bake the model into the image (126MB)
COPY yolo26x-pose.pt /app/yolo26x-pose.pt

# Copy app code last to bust cache on code changes
COPY server.py /app/server.py

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
