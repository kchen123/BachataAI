@echo off
echo.
echo === Step 1: Granting Cloud Run permission to read DB password ===
call gcloud projects add-iam-policy-binding leumeah --member=serviceAccount:1001979371056-compute@developer.gserviceaccount.com --role=roles/secretmanager.secretAccessor
if errorlevel 1 goto :error

echo.
echo === Step 2: Deploying BachataAI to Cloud Run (takes 3-5 min) ===
call gcloud run deploy bachataai --source . --region us-central1 --allow-unauthenticated --memory 2Gi --cpu 2 --timeout 300 --add-cloudsql-instances leumeah:us-central1:leumeah --set-env-vars CLOUD_SQL_CONNECTION_NAME=leumeah:us-central1:leumeah,DB_USER=postgres,DB_NAME=postgres --set-secrets DB_PASS=db-password:latest
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
