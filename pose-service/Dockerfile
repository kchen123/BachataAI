FROM us-central1-docker.pkg.dev/leumeah/cloud-run-source-deploy/bachataai-pose-base:latest

WORKDIR /app

# Only server.py changes between deploys — everything else is in the base image
COPY server.py /app/server.py

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
