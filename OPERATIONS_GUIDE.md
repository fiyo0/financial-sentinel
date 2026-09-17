# 🛡️ Financial Sentinel — Operational & Developer Guide

This document serves as the single source of truth for repository conventions, containerization, cloud deployments, security policies, and developer workflows.

---

## 📁 1. Project Coordinates & Environment

* **Workspace Root:** `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`
* **Python Environment:** `.venv`
  * Python binary: `.venv/bin/python3`
  * Test runner: `.venv/bin/pytest`
* **Local gcloud Binary & Python:**
  * SDK Path: `/Users/frank/google-cloud-sdk/bin/gcloud`
  * Python Runtime: `CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3`
  * Mandatory Attribution: `CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"`
* **GCP Infrastructure:**
  * **Project ID:** `financial-sentinel-507007` (Number: `272533633552`)
  * **Region:** `us-central1`
  * **Cloud Run Service:** `financial-sentinel`
  * **Production URL:** `https://financial-sentinel-272533633552.us-central1.run.app`
  * **GCS State Bucket:** `gs://financial-sentinel-data-507007`

---

## 🐳 2. Docker & Container Architecture

### `Dockerfile`
* **Base Image:** `python:3.11-slim`
* **Workdir:** `/app`
* **Security (Non-Root User):** Runs as unprivileged user `sentinel:sentinel` (UID 1000, GID 1000). Never run container processes as root.
* **Storage Prep:** Automatically creates `/app/storage` and `/app/data` directories owned by `sentinel`.
* **Runtime Command:**
  ```dockerfile
  CMD ["sh", "-c", "python -m uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
  ```
  * Uvicorn dynamically binds to the `$PORT` environment variable injected by Cloud Run (defaulting to 8080).

### `.dockerignore`
* Strictly excludes all:
  * Virtual environments (`.venv/`, `venv/`)
  * Git history (`.git/`, `.gitignore`)
  * Local databases (`*.db`, `storage/*.db*`, `*.sqlite*`, WAL/SHM files)
  * Secrets & environment files (`.env`, `*.env`)
  * Test directories (`tests/`, `.pytest_cache/`)
  * User-specific local portfolios (`data/my_portfolio*`, `data/telegram_chat_id.txt`)
  * Build caches (`__pycache__/`, `.ruff_cache/`)

---

## 🔒 3. Git Rules & `.gitignore`

* **Never Commit Databases:** SQLite state databases contain user data, hashes, and session tokens. All `*.db`, `*.db-*`, `storage/*.db*` are strictly gitignored.
* **Never Commit Secrets:** `.env`, `.env.*`, `*.env` are excluded. Secrets are managed exclusively via Google Cloud Secret Manager in production.
* **Always Keep `main` Clean:** Changes must be verified by `.venv/bin/pytest` prior to committing.
* **Commit Conventions:** Follow conventional commits:
  * `feat(...)`: New user-facing capabilities or agent features
  * `fix(...)`: Bug fixes and error remediations
  * `chore(...)`: Deployment scripts, dependencies, build configs
  * `test(...)`: Unit/integration test updates

---

## ☁️ 4. Google Cloud Run: How & When to Deploy

### When to Deploy
1. **Never deploy on broken tests:** All 127 tests must pass locally via `.venv/bin/pytest`.
2. **Never deploy uncommitted work:** Git working tree should be committed so the container matches a specific git commit SHA.
3. **Deploy after completing milestones:** e.g., UI updates, new agent integrations, bug fixes, or scheduler modifications.

### Deployment Process (2 Steps)

#### Step A: Container Build via Google Cloud Build
Run outside sandbox (`BypassSandbox: true`):
```bash
CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3 \
CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity" \
/Users/frank/google-cloud-sdk/bin/gcloud builds submit \
  --tag "gcr.io/financial-sentinel-507007/financial-sentinel" . \
  --project=financial-sentinel-507007
```

#### Step B: Rollout to Google Cloud Run
```bash
CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3 \
CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity" \
/Users/frank/google-cloud-sdk/bin/gcloud run deploy financial-sentinel \
  --image "gcr.io/financial-sentinel-507007/financial-sentinel" \
  --platform managed \
  --region "us-central1" \
  --project "financial-sentinel-507007" \
  --allow-unauthenticated \
  --max-instances=1 \
  --no-cpu-throttling \
  --set-env-vars "DASHBOARD_AUTH_ENABLED=true,TELEGRAM_ALLOWED_USERNAMES=forello0,SERVICE_URL=https://financial-sentinel-272533633552.us-central1.run.app,GEMINI_MODEL=gemini-3.8-flash,TELEGRAM_CHAT_ID=5251594125,GCS_DATA_BUCKET=financial-sentinel-data-507007,GCS_SYNC_ENABLED=true" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,DASHBOARD_PASSWORD=dashboard-password:latest,APP_SECRET_KEY=app-secret-key:latest,CRON_SECRET=cron-secret:latest" \
  --memory 1Gi \
  --cpu 1
```

### Critical Cloud Run Deployment Flags:
* **DO NOT set `--set-env-vars PORT=...`**: `PORT` is a reserved system variable in Cloud Run. Setting it causes an immediate deployment failure (`The following reserved env names were provided: PORT`).
* **`--max-instances=1` (MANDATORY)**: Financial Sentinel uses SQLite in WAL mode with bidirectional GCS cloud sync (`backup_to_gcs` / `download_from_gcs`). Running multiple instances risks split-brain database corruption. Keep max instances locked to 1.
* **`--no-cpu-throttling` (MANDATORY)**: Cloud Run normally suspends CPU when no incoming HTTP requests exist. Because Financial Sentinel runs background cron schedulers (`scheduler.py`) and background GCS backup sync threads, CPU throttling must remain DISABLED so background tasks run on time.

---

## 🔐 5. Environment Variables & Secret Manager Mapping

| Variable / Secret | Type | Source | Purpose |
| :--- | :--- | :--- | :--- |
| `DASHBOARD_AUTH_ENABLED` | Env Var | Literal (`true`) | Enforces session cookie authentication on dashboard routes |
| `SERVICE_URL` | Env Var | Literal | Full HTTPS URL of Cloud Run service for webhooks and redirects |
| `GEMINI_MODEL` | Env Var | Literal (`gemini-3.8-flash`) | Foundation LLM model for synthesis agents |
| `TELEGRAM_ALLOWED_USERNAMES` | Env Var | Literal (`forello0`) | Whitelisted Telegram usernames authorized to interact with bot |
| `TELEGRAM_CHAT_ID` | Env Var | Literal (`5251594125`) | Default push recipient for scheduled briefings |
| `GCS_DATA_BUCKET` | Env Var | Literal (`financial-sentinel-data-507007`) | GCS bucket for persistent SQLite snapshots |
| `GCS_SYNC_ENABLED` | Env Var | Literal (`true`) | Enables live periodic and on-mutation sync to GCS |
| `GEMINI_API_KEY` | Secret | Secret Manager (`gemini-api-key:latest`) | Fallback system Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Secret | Secret Manager (`telegram-bot-token:latest`) | Telegram Bot API authentication token |
| `DASHBOARD_PASSWORD` | Secret | Secret Manager (`dashboard-password:latest`) | Admin login password |
| `APP_SECRET_KEY` | Secret | Secret Manager (`app-secret-key:latest`) | AES-256 Fernet key for encrypting user keys and signing session tokens |
| `CRON_SECRET` | Secret | Secret Manager (`cron-secret:latest`) | Bearer token authenticating Cloud Scheduler webhooks |

---

## ⚠️ 6. What to Do & What NOT to Do

### What to DO:
1. **Always run tests locally:** Before pushing code, execute `.venv/bin/pytest`.
2. **Preserve audit remediation rules:** The platform strictly adheres to 11 security findings documented in `AUDIT_DOSSIER.md`:
   * AES-256-GCM encryption for all stored user API keys (BYOK).
   * Token epochs for immediate session invalidation on credential rotation.
   * Parameterized SQL queries only (zero string interpolation).
   * Path traversal protection (`validate_safe_path`).
3. **Keep temporal grounding sharp:** All briefing prompts must pass `as_of=now_pst` and inject `economic_calendar` ground truth so reports never hallucinate past events as "upcoming."
4. **Use `deploy/deploy_gcp.sh`:** Automated script that bundles the full env vars and secrets.

### What NOT to DO:
1. **DO NOT run global `pip install`:** Always install packages in `.venv`.
2. **DO NOT commit `.env` or `*.db` files:** All production secrets live in Secret Manager; database state lives in GCS.
3. **DO NOT remove `--no-cpu-throttling` on Cloud Run:** Background briefing schedulers will freeze.
4. **DO NOT increase Cloud Run `--max-instances` above 1:** Multiple instances will write to the same local SQLite file independently and corrupt GCS state.
5. **DO NOT pass `PORT` in `--set-env-vars`:** Cloud Run reserves this name.
6. **DO NOT delete or truncate database tables directly:** Always use `StateStore` methods with automatic checkpointing and GCS sync.
