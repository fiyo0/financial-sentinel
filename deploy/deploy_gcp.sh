#!/bin/bash
# ==============================================================================
# FINANCIAL SENTINEL — GOOGLE CLOUD RUN PRODUCTION DEPLOYMENT
# ==============================================================================

set -e

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
REGION="us-central1"
SERVICE_NAME="financial-sentinel"

if [ -z "$PROJECT_ID" ]; then
    echo "⚠️ No GCP project configured. Run 'gcloud config set project YOUR_PROJECT_ID' first."
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
    --set-env-vars "PORT=8000,DASHBOARD_AUTH_ENABLED=true,TELEGRAM_ALLOWED_USERNAMES=forello0,SERVICE_URL=https://financial-sentinel-272533633552.us-central1.run.app,GEMINI_MODEL=gemini-3.8-flash,TELEGRAM_CHAT_ID=5251594125,GCS_DATA_BUCKET=financial-sentinel-data-507007,GCS_SYNC_ENABLED=true" \
    --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,DASHBOARD_PASSWORD=dashboard-password:latest,APP_SECRET_KEY=app-secret-key:latest,CRON_SECRET=cron-secret:latest" \
    --memory 1Gi \
    --cpu 1

echo "✅ Deployment complete! Service URL generated with automatic TLS 1.3 encryption."
