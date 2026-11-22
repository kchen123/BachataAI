FROM us-central1-docker.pkg.dev/leumeah/cloud-run-source-deploy/bachataai-pose-base:latest

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server.py /app/server.py

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
