@echo off
echo.
echo === Step 1: Copy model into build context ===
if not exist yolo26x-pose.pt (
    copy ..\yolo26x-pose.pt yolo26x-pose.pt
)
if errorlevel 1 goto :error

echo.
echo === Step 2: Deploying Pose Service to Cloud Run with GPU ===
call gcloud run deploy bachataai-pose --source . --region us-central1 --no-allow-unauthenticated --memory 32Gi --cpu 8 --gpu 1 --gpu-type nvidia-l4 --max-instances 1 --concurrency 1 --timeout 1800
if errorlevel 1 goto :error

echo.
echo === Step 3: Granting Flask app permission to invoke pose service ===
call gcloud run services add-iam-policy-binding bachataai-pose --region us-central1 --member=serviceAccount:1001979371056-compute@developer.gserviceaccount.com --role=roles/run.invoker
if errorlevel 1 goto :error

echo.
echo === DONE! Pose service URL is shown above. ===
echo === Update POSE_SERVICE_URL in the Flask app deploy script. ===
pause
exit /b 0

:error
echo.
echo === ERROR occurred. See output above. ===
pause
exit /b 1
