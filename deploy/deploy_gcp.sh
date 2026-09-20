#!/bin/bash
# ==============================================================================
# FINANCIAL SENTINEL — GOOGLE CLOUD RUN PRODUCTION DEPLOYMENT
# ==============================================================================

set -e

if ! command -v gcloud &>/dev/null; then
    export PATH="/Users/frank/google-cloud-sdk/bin:$PATH"
fi
export CLOUDSDK_PYTHON="${CLOUDSDK_PYTHON:-/Users/frank/python311/bin/python3.11}"
export CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "${GCP_PROJECT_ID:-financial-sentinel-507007}")
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${GCP_SERVICE_NAME:-financial-sentinel}"
GCS_DATA_BUCKET="${GCS_DATA_BUCKET:-financial-sentinel-data-507007}"
TELEGRAM_ALLOWED_USERNAMES="${TELEGRAM_ALLOWED_USERNAMES:-forello0}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-5251594125}"
SERVICE_URL="${SERVICE_URL:-https://financial-sentinel-272533633552.us-central1.run.app}"

if [ -z "$PROJECT_ID" ]; then
    echo "⚠️ No GCP project configured. Run 'gcloud config set project YOUR_PROJECT_ID' or export GCP_PROJECT_ID first."
    exit 1
fi

echo "🛡️ Deploying Financial Sentinel to Google Cloud Run (Project: $PROJECT_ID, Region: $REGION)..."

# Build container using Cloud Build
gcloud builds submit --tag "gcr.io/$PROJECT_ID/$SERVICE_NAME" .

# Deploy to Cloud Run with HTTPS, single-writer concurrency, and Secret Manager references
gcloud run deploy "$SERVICE_NAME" \
    --image "gcr.io/$PROJECT_ID/$SERVICE_NAME" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated \
    --max-instances=1 \
    --no-cpu-throttling \
    --set-env-vars "DASHBOARD_AUTH_ENABLED=${DASHBOARD_AUTH_ENABLED:-true},SERVICE_URL=${SERVICE_URL},GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash},TELEGRAM_ALLOWED_USERNAMES=${TELEGRAM_ALLOWED_USERNAMES},TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID},GCS_DATA_BUCKET=${GCS_DATA_BUCKET},GCS_SYNC_ENABLED=true" \
    --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,DASHBOARD_PASSWORD=dashboard-password:latest,APP_SECRET_KEY=app-secret-key:latest,CRON_SECRET=cron-secret:latest" \
    --memory 1Gi \
    --cpu 1

echo "✅ Deployment complete! Service URL generated with automatic TLS 1.3 encryption."

