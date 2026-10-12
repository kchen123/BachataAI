@echo off
set POSE_SERVICE_URL=https://bachataai-pose-1001979371056.us-central1.run.app
echo Starting BachataAI dev server...
echo GPU Service: %POSE_SERVICE_URL%
echo.
venv\Scripts\python core\app.py
