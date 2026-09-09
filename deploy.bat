@echo off
echo.
echo === Step 1: Deploying BachataAI to Cloud Run (takes 3-5 min) ===
call gcloud run deploy bachataai --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 2 --timeout 300
if errorlevel 1 goto :error

echo.
echo === DONE! Your app URL is shown above. ===
pause
exit /b 0

:error
echo.
echo === ERROR occurred. See output above. ===
pause
exit /b 1
