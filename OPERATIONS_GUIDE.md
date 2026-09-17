# 🛡️ Financial Sentinel: Comprehensive Developer & Operational Handbook

> **System Version:** `2.4.0` | **Target Model:** Gemini 3.8 Flash | **Active Platform:** Google Cloud Run (`us-central1`)  
> **Repository Root:** `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`  
> **Production Service:** `https://financial-sentinel-272533633552.us-central1.run.app`  
> **Audit Dossier Reference:** [`AUDIT_DOSSIER.md`](AUDIT_DOSSIER.md)

---

## 📑 Table of Contents
1. [System Mission & Architectural Philosophy](#1-system-mission--architectural-philosophy)
2. [Project Coordinates & Local Runtime Topology](#2-project-coordinates--local-runtime-topology)
3. [Containerization & Docker Architecture](#3-containerization--docker-architecture)
4. [Git Discipline & Repository Hygiene](#4-git-discipline--repository-hygiene)
5. [Persistence & Data Durability (Dual-Layer SQLite + GCS Sync)](#5-persistence--data-durability-dual-layer-sqlite--gcs-sync)
6. [Security & Cryptographic Invariants (Audit Remediation Matrix)](#6-security--cryptographic-invariants-audit-remediation-matrix)
7. [Temporal Grounding & Macroeconomic Calendar Engine](#7-temporal-grounding--macroeconomic-calendar-engine)
8. [Multi-Agent Pipeline & Domain Services](#8-multi-agent-pipeline--domain-services)
9. [Google Cloud Run Deployment Playbook](#9-google-cloud-run-deployment-playbook)
10. [Environment Variables & Secret Manager Mappings](#10-environment-variables--secret-manager-mappings)
11. [Troubleshooting, Gotchas & Hard-Won Lessons](#11-troubleshooting-gotchas--hard-won-lessons)
12. [Checklist for New AI Agents & Engineers](#12-checklist-for-new-ai-agents--engineers)

---

## 1. System Mission & Architectural Philosophy

Financial Sentinel is an autonomous, multi-agent investment intelligence and portfolio risk governance platform. Its core architecture separates **deterministic mathematical verification** from **probabilistic generative AI**:

```
                                  [ Live Market Feeds ]
           (Robinhood Quotes, StockTwits Streams, SEC 8-K, CNBC, MarketWatch, Nasdaq API)
                                            │
                                            ▼
                                ┌───────────────────────┐
                                │ 1. News Ingestion     │ ──► [ Persistent SQLite State Store ]
                                │    & Market Data      │     (WAL Mode, Checkpointed GCS Sync)
                                └───────────┬───────────┘
                                            │
                      ┌─────────────────────┴───────────────────────┐
                      ▼                                             ▼
        ┌───────────────────────────┐                 ┌───────────────────────────┐
        │ 2. Portfolio Risk Agent   │                 │ 3. Opportunity Hunter     │
        │    (Quant VaR, Beta, HHI, │                 │    (Thematic Alpha,       │
        │     Kelly Sizing, Ripple) │                 │     Moonshot Discovery)   │
        └─────────────┬─────────────┘                 └─────────────┬─────────────┘
                      │                                             │
                      └─────────────────────┬───────────────────────┘
                                            ▼
                                ┌───────────────────────────┐
                                │ 4. Adversarial Critic     │
                                │    (Stress-Test Theses,   │
                                │     Confidence Hurdle)    │
                                └───────────┬───────────────┘
                                            │
                                            ▼
                                ┌───────────────────────────┐
                                │ 5. CIO Synthesis Agent    │
                                │    (Gemini 3.8 Flash,     │
                                │     Token Budget Guard)   │
                                └───────────┬───────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
      ┌───────────────────────────┐                   ┌───────────────────────────┐
      │ Web Console (FastAPI)     │                   │ Telegram Terminal Bot     │
      │ • Live SSE Telemetry      │                   │ • Shorthand /<ticker>     │
      │ • 6-Tab Institutional UI  │                   │ • /portfolio, /cash       │
      │ • Deep Dive Studio        │                   │ • Scheduled Push Crons    │
      └───────────────────────────┘                   └───────────────────────────┘
```

### Core Architectural Invariants:
1. **Deterministic Indicators First:** Technical metrics (RSI-14, MACD, Bollinger Bands, Moving Average spreads) are computed mathematically via Python numpy/scipy routines. The LLM *never* computes or approximates numbers.
2. **Adversarial Stress-Testing:** The Opportunity Hunter's bullish recommendations are subjected to the Adversarial Critic agent, which assigns devil's advocate counter-theses and enforces strict risk-reward confidence hurdles before recommendations reach the CIO synthesis report.
3. **Temporal Discipline:** The system rejects date hallucinations. LLM prompts are anchored to physical ground truth (PST/EDT session times, deterministic economic calendar events, and relative news timestamps).
4. **Single-Writer Concurrency:** To preserve SQLite state consistency, Cloud Run is locked to `--max-instances=1` with continuous background execution (`--no-cpu-throttling`).

---

## 2. Project Coordinates & Local Runtime Topology

### Local Machine (macOS)
* **Workspace Path:** `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`
* **Python Virtual Environment:** `.venv`
  * Python interpreter: `.venv/bin/python3`
  * Test execution binary: `.venv/bin/pytest`
* **Local Google Cloud SDK:**
  * Binary: `/Users/frank/google-cloud-sdk/bin/gcloud`
  * Python wrapper: `CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3`
  * Resource attribution prefix:
    ```bash
    CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity"
    ```

### Production Cloud Infrastructure
* **GCP Project ID:** `financial-sentinel-507007` (Project Number: `272533633552`)
* **Region:** `us-central1`
* **Cloud Run Service:** `financial-sentinel`
* **Active Revision:** `financial-sentinel-00107-b7h`
* **Public Service URL:** `https://financial-sentinel-272533633552.us-central1.run.app`
* **Google Cloud Storage Bucket:** `gs://financial-sentinel-data-507007` (Single source of truth for persistent SQLite database backups)

---

## 3. Containerization & Docker Architecture

### `Dockerfile` Walkthrough
```dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CRITICAL SECURITY RULE: Unprivileged user (C-11)
RUN groupadd -g 1000 sentinel && \
    useradd -u 1000 -g sentinel -m -s /bin/bash sentinel && \
    mkdir -p storage data && \
    chown -R sentinel:sentinel /app

USER sentinel

CMD ["sh", "-c", "python -m uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

### Key Container Invariants:
1. **Non-Root Execution:** The container explicitly switches to `USER sentinel` (UID 1000). The service never runs as root in production.
2. **Directory Pre-allocation:** Both `/app/storage` (SQLite) and `/app/data` (runtime cache) are created and owned by `sentinel:sentinel` prior to runtime invocation.
3. **Dynamic Port Binding:** Uvicorn binds to `${PORT:-8080}`. Cloud Run injects the `$PORT` variable automatically at container startup.

### `.dockerignore` Exclusion Rules
The container build strictly excludes all ephemeral and sensitive files:
* Virtual environments (`.venv/`, `venv/`)
* Git history and metadata (`.git/`, `.gitignore`)
* Environment files (`.env`, `*.env`)
* Local databases (`*.db`, `storage/*.db*`, `*.sqlite`, WAL/SHM files)
* Test directories (`tests/`, `.pytest_cache/`, `.hypothesis/`)
* Local portfolio CSVs (`data/my_portfolio*`)

---

## 4. Git Discipline & Repository Hygiene

### `.gitignore` Architecture
Financial Sentinel enforces strict data boundaries in `.gitignore`:
* **Secrets:** `.env`, `.env.*`, `*.env` are completely excluded. Never commit API keys.
* **Databases:** All SQLite files (`*.db`, `*.sqlite`, `*.db-wal`, `*.db-shm`) are excluded. Committing local databases risks leaking credentials and session tokens.
* **Personal Data:** `data/my_portfolio.csv` and `data/telegram_chat_id.txt` are excluded.

### Commit Guidelines:
* Every commit must be tested locally via `.venv/bin/pytest` prior to `git commit`.
* Use Conventional Commits:
  * `feat(...)`: New user-facing capability, agent, or UI component.
  * `fix(...)`: Bug fix, timing adjustment, or security patch.
  * `chore(...)`: Deployment script, dependency bump, or build setting.
  * `docs(...)`: Documentation or audit dossier updates.
  * `test(...)`: Unit/integration test modifications.

---

## 5. Persistence & Data Durability (Dual-Layer SQLite + GCS Sync)

### Database Configuration:
Financial Sentinel utilizes SQLite 3 configured for high-concurrency production workloads in [`storage/state_store.py`](storage/state_store.py):
* **Journal Mode:** Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) allows concurrent readers while a writer is executing.
* **Synchronous Setting:** `PRAGMA synchronous=NORMAL;` balances write performance with crash resilience.
* **Foreign Key Constraints:** `PRAGMA foreign_keys=ON;` enforces referential integrity across users, portfolios, deep dives, and briefings.

### Google Cloud Storage (GCS) Sync Lifecycle:
Because Cloud Run containers have ephemeral local disk storage, state durability is guaranteed via bidirectional GCS replication:

```
[ Container Boot ]
        │
        ▼
download_from_gcs() ──► Checks gs://financial-sentinel-data-507007/state.db
                        • If found: Downloads and replaces local storage/state.db
                        • If absent: Seeds clean database and initializes default admin

[ Runtime Mutation / Checkpoint ]
        │
        ▼
backup_to_gcs() ──────► 1. Executes online atomic backup: conn.backup() to temporary file
                        2. Uploads clean snapshot to gs://financial-sentinel-data-507007/state.db
                        3. Checkpoints WAL journal
```

### Critical Concurrency Invariant: Single-Writer Lock
> [!CAUTION]
> **Cloud Run `--max-instances=1` is MANDATORY.**
> Because SQLite is an embedded database and syncs state to GCS as a complete file snapshot, running $>1$ instance creates an immediate **split-brain condition** where two containers overwrite each other's state in GCS. Never scale instances above 1 without migrating to Cloud SQL.

---

## 6. Security & Cryptographic Invariants (Audit Remediation Matrix)

Financial Sentinel passed an external security audit resolving 11 critical and high-priority vulnerabilities documented in [`AUDIT_DOSSIER.md`](AUDIT_DOSSIER.md):

| Finding | Vulnerability | Architectural Remediation |
| :--- | :--- | :--- |
| **C-1** | Multi-tenant BYOK key leakage | Per-user Gemini API keys are encrypted at rest using AES-256 (Fernet) backed by `APP_SECRET_KEY` in Secret Manager. |
| **C-2** | Plaintext password risk | PBKDF2-HMAC-SHA256 password hashing with 100,000 rounds and random salt. |
| **C-3** | Indefinite session hijacking | `token_epoch` integer per user. Incrementing epoch immediately revokes all active session cookies. |
| **C-4** | Privilege escalation | Role-Based Access Control (`role = 'admin'` vs `'user'`). Hardened `/api/users` and admin endpoints. |
| **C-5** | SQL Injection | 100% Parameterized queries across `storage/state_store.py`. Zero SQL string concatenation. |
| **C-6** | Path Traversal | `validate_safe_path()` verifies all file operations stay strictly inside `/app/data` or `/app/storage`. |
| **C-7** | Brute-force DoS & memory leaks | IP-based sliding window rate limiter on login with periodic stale-IP cache pruning. |
| **C-8** | Insecure session cookies | Cookies set with `HttpOnly=True`, `SameSite="lax"`, `Secure=True` (on HTTPS/Cloud Run), and 7-day max-age. |
| **C-9** | Missing Security Headers | Middleware injects strict Content Security Policy (CSP), HSTS, X-Frame-Options (`DENY`), and nosniff. |
| **C-10** | WAL Read Tearing on GCS Sync | SQLite snapshotting uses `conn.backup()` before upload rather than raw file copying. |
| **C-11** | Hardcoded legacy admin passwords | `rotate_legacy_admin_credentials()` automatically rotates default `sentinel_admin` credentials on boot. |

---

## 7. Temporal Grounding & Macroeconomic Calendar Engine

### The Problem Solved: Temporal Lag & Hallucinations
In financial intelligence, LLMs frequently describe past events as "upcoming tomorrow" due to:
1. Zero temporal grounding in prompts (the LLM does not know what day "today" is).
2. Unordered, un-timestamped news headlines (preview articles from yesterday masquerade as breaking news).
3. UTC vs. local market time confusion (a 2:00 PM EDT Fed decision is 6:00 PM UTC, causing date boundary rollovers).

### The 4-Pillar Temporal Solution:
1. **Strict Temporal Directives:** `BRIEFING_COMMUNICATION_RULES` Rule 3 mandates exact date anchoring across all briefings.
2. **Session Ground Truth Injection:** Every briefing prompt header injects:
   * Current Calendar Date (e.g. `Wednesday, September 16, 2026`)
   * Current Times: `03:00 PM PDT | 06:00 PM EDT`
   * Trading Session Phase: `Closed / Post-Market Wrap`
3. **Timestamped Relative News:** Headlines are formatted chronologically with age tags:
   * `[Today 02:00 PM EDT (4.0h ago)] Fed raises rates by 25bps...`
   * `[Yesterday Sep 15 (29.0h ago)] Fed preview: What to expect...`
4. **Deterministic Macroeconomic Calendar ([`analytics/economic_calendar.py`](analytics/economic_calendar.py)):**
   * Pre-compiled official schedule of FOMC rate decisions (2025–2027), Chair press conferences, and monthly BLS releases (CPI, PPI, Jobs/NFP, GDP).
   * Dynamically evaluates status against `datetime.now()`:
     * `[COMPLETED]` (e.g. concluded 5.2h ago at 02:00 PM EDT)
     * `[IMMINENT]` (scheduled for later today)
     * `[TOMORROW]` or `[UPCOMING]`

---

## 8. Multi-Agent Pipeline & Domain Services

### Agent Responsibilities:
* **`NewsIngestionAgent` ([`agents/news_ingestion.py`](agents/news_ingestion.py)):** Polls RSS feeds, CNBC, MarketWatch, SEC 8-K filings, and StockTwits sentiment. Deduplicates items and stores in SQLite.
* **`PortfolioRiskAgent` ([`agents/portfolio_risk.py`](agents/portfolio_risk.py)):** Evaluates portfolio concentration (HHI), sector exposure, beta, and supply-chain ripple effects.
* **`OpportunityHunterAgent` ([`agents/opportunity_hunter.py`](agents/opportunity_hunter.py)):** Surfaces thematic alpha and asymmetric moonshots with strict downside risk boundaries.
* **`AdversarialCriticAgent` ([`agents/adversarial_critic.py`](agents/adversarial_critic.py)):** Stress-tests recommendations, challenges bullish bias, and verifies data provenance.
* **`CIOSynthesisAgent` ([`agents/cio_synthesis.py`](agents/cio_synthesis.py)):** Synthesizes final executive reports under the protection of `TokenBudgetGuard` ([`analytics/token_budget.py`](analytics/token_budget.py)).

### Decoupled Domain Services ([`services/`](services/)):
* `IdentityService`: User authentication, session token generation, BYOK key encryption/decryption.
* `PortfolioService`: Positions, transactions, cash balances, and valuations.
* `AnalysisService`: Ticker analysis coordination, technical indicators, sentiment streams, and deep-dive archiving.
* `BriefingService`: Briefing lifecycle, scheduled runs, and manual trigger dispatches.

---

## 9. Google Cloud Run Deployment Playbook

### Pre-Deployment Verification Checklist:
1. `.venv/bin/pytest` passes 100% (127/127 tests).
2. `git status` shows a clean working tree.
3. Secret Manager bindings are verified.

### Deployment Step 1: Container Build via Cloud Build
Run in terminal (outside sandbox, `BypassSandbox: true`):
```bash
CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3 \
CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity" \
/Users/frank/google-cloud-sdk/bin/gcloud builds submit \
  --tag "gcr.io/financial-sentinel-507007/financial-sentinel" . \
  --project=financial-sentinel-507007
```

### Deployment Step 2: Rollout to Cloud Run
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

*(Or simply execute `./deploy/deploy_gcp.sh`)*

---

## 10. Environment Variables & Secret Manager Mappings

### Standard Environment Variables:
* `DASHBOARD_AUTH_ENABLED=true`: Enforces session cookie verification.
* `SERVICE_URL=https://financial-sentinel-272533633552.us-central1.run.app`: Public HTTPS endpoint.
* `GEMINI_MODEL=gemini-3.8-flash`: Primary reasoning engine.
* `TELEGRAM_ALLOWED_USERNAMES=forello0`: Access control whitelist for Telegram bot.
* `TELEGRAM_CHAT_ID=5251594125`: Default push channel.
* `GCS_DATA_BUCKET=financial-sentinel-data-507007`: GCS persistence target.
* `GCS_SYNC_ENABLED=true`: Enables state checkpointing to GCS.

### Secret Manager References:
* `GEMINI_API_KEY` $\leftarrow$ `projects/financial-sentinel-507007/secrets/gemini-api-key:latest`
* `TELEGRAM_BOT_TOKEN` $\leftarrow$ `projects/financial-sentinel-507007/secrets/telegram-bot-token:latest`
* `DASHBOARD_PASSWORD` $\leftarrow$ `projects/financial-sentinel-507007/secrets/dashboard-password:latest`
* `APP_SECRET_KEY` $\leftarrow$ `projects/financial-sentinel-507007/secrets/app-secret-key:latest`
* `CRON_SECRET` $\leftarrow$ `projects/financial-sentinel-507007/secrets/cron-secret:latest`

---

## 11. Troubleshooting, Gotchas & Hard-Won Lessons

### 1. The Cloud Run `PORT` Flag Trap
* **Symptom:** `gcloud run deploy` fails with: `spec.template.spec.containers[0].env: The following reserved env names were provided: PORT`.
* **Fix:** Cloud Run automatically sets `PORT=8080`. **Never** pass `PORT=...` in `--set-env-vars`.

### 2. The CPU Throttling Trap
* **Symptom:** Scheduled market briefings miss their trigger times, or GCS backup sync hangs indefinitely.
* **Fix:** Cloud Run suspends CPU allocation on inactive instances by default. `--no-cpu-throttling` must always be specified.

### 3. Fast Network Perceptual Speed Trap
* **Symptom:** User clicks "Refresh" on the UI and complains nothing happened or the button is dead.
* **Root Cause:** In-memory APIs respond in $<15\text{ ms}$. If the DOM updates with identical data, zero pixels visibly change on a 60Hz display.
* **Fix:** All manual refresh buttons must include:
  1. `disabled = true` and `animate-spin` loading states.
  2. A minimum perceptual delay (~400ms) via `Promise.all`.
  3. A visible "Last Synced: HH:MM:SS" timestamp badge.
  4. Momentary container dimming and top banner confirmation (`showBanner`).

### 4. macOS Python LibreSSL vs. OpenSSL Warning
* **Symptom:** Warning during pytest: `NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+, currently the 'ssl' module is compiled with 'LibreSSL 2.8.3'`.
* **Impact:** Harmless local dev warning due to macOS system Python linking to LibreSSL. The production Docker container runs `python:3.11-slim` with genuine OpenSSL.

---

## 12. Checklist for New AI Agents & Engineers

When initiating a new session or feature branch:
1. [ ] **Verify Working Directory:** Confirm Cwd is `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`.
2. [ ] **Run Baseline Tests:** Run `.venv/bin/pytest` and verify 127/127 tests pass before making any changes.
3. [ ] **Respect the Threat Model:** Never introduce raw SQL formatting; use parameterized queries only.
4. [ ] **Preserve Single-Writer Constraint:** Never suggest multi-instance autoscaling without a Cloud SQL migration plan.
5. [ ] **Ground All Briefing Prompts:** Always supply `as_of=now_pst` and inject `economic_calendar` ground truth into LLM prompts.
6. [ ] **Deploy Safely:** Ensure all tests pass, commit to git, and use the 2-step Cloud Build + Cloud Run procedure.
