# AI Agent System Briefing & Production Deployment Guide

This document is the onboarding manual for AI agents operating on the **Financial Sentinel & Alpha Multi-Agent System (v2.4.0)**. It details the project architecture, operational invariants, development conventions, and exact production deployment procedures.

---

## 1. Quick Orientation

- **Workspace Root**: `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`
- **Git Repository**: `https://github.com/fiyo0/financial-sentinel.git` (Primary branch: `main`)
- **Runtime Environment**: Python 3.14.7 (`.venv`) on macOS; containerized as `python:3.11-slim` in production.
- **Production URL**: `https://financial-sentinel-272533633552.us-central1.run.app`
- **GCP Project**: `financial-sentinel-507007` (Region: `us-central1`)
- **GCS Persistence Bucket**: `financial-sentinel-data-507007`

---

## 2. Platform Architecture

Financial Sentinel is an autonomous, multi-agent quantitative financial intelligence platform:

```
                          ┌───────────────────────────┐
                          │   News & Market Ingestion │
                          │  (RSS, SEC, StockTwits)   │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                          ┌───────────────────────────┐
                          │  Quant Risk & Opportunity │
                          │  (VaR, Beta, ATR, Radar)  │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                          ┌───────────────────────────┐
                          │ Adversarial Risk Critic   │
                          │ (Anti-Rubberstamp Audit)  │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                          ┌───────────────────────────┐
                          │    CIO Synthesis Agent    │
                          │ (Pre/Mid/Post/EOD Briefs) │
                          └─────────────┬─────────────┘
                                        │
                   ┌────────────────────┴────────────────────┐
                   ▼                                         ▼
     ┌───────────────────────────┐             ┌───────────────────────────┐
     │ FastAPI 6-Tab Web Console │             │   Telegram Terminal Bot   │
     │ (Live SSE Stream, BYOK)   │             │ (Encrypted Webhook, /<tk>)│
     └───────────────────────────┘             └───────────────────────────┘
```

### Core Agents (`agents/`)
1. **`NewsIngestionAgent` ([`agents/news_ingestion.py`](agents/news_ingestion.py))**: Scrapes RSS feeds, financial wires, and SEC press/regulatory releases. Computes SHA-256 deduplication hashes and maps company aliases to portfolio holdings without price gating.
2. **`MarketBriefingAgent` ([`agents/market_briefing_agent.py`](agents/market_briefing_agent.py))**: Generates Pre-Market (6:30 AM PST), Mid-Market (10:00 AM PST), Post-Market (3:00 PM PST), and Weekend macro briefings. Uses catalyst-first framing, cross-briefing memory, concrete SPY/QQQ technical bands (50-DMA, ATR-14, RSI-14), and BMO/AMC earnings schedules.
3. **`PortfolioRiskAgent` ([`agents/portfolio_risk.py`](agents/portfolio_risk.py))**: Computes 95%/99% Parametric Value-at-Risk (VaR), Herfindahl-Hirschman sector concentration (HHI), and supply-chain ripple vulnerability.
4. **`OpportunityHunterAgent` ([`agents/opportunity_hunter.py`](agents/opportunity_hunter.py))**: Scans for asymmetric risk/reward setups outside the portfolio.
5. **`AdversarialCriticAgent` ([`agents/adversarial_critic.py`](agents/adversarial_critic.py))**: Anti-rubberstamp risk auditor testing candidate ideas against valuation multiples and thesis invalidation criteria.
6. **`CIOSynthesisAgent` ([`agents/cio_synthesis.py`](agents/cio_synthesis.py))**: Produces unified executive reports under strict token budget constraints.
7. **`TelegramBot` ([`agents/telegram_bot.py`](agents/telegram_bot.py))**: Command terminal (`/<ticker>`, `/portfolio`, `/cash`, `/briefing`) with HMAC webhook security.

### Core Domain Services & Analytics
- **`storage/state_store.py`**: SQLite database (`storage/state.db`) configured in WAL mode, thread-local connections, and 30-second busy timeout. Auto-restores from GCS on boot and captures non-blocking point-in-time snapshots via SQLite's Online Backup API (`src.backup(dst)`) on write.
- **`analytics/sentiment_stream.py`**: Scrapes StockTwits & Reddit, computes message velocity and RVOL, and runs batch comment sentiment classification on **Gemini 3.1 Flash-Lite** with transparent regex fallback.
- **`analytics/technical_indicators.py`**: Deterministic calculations for RSI-14, MACD, Bollinger Bands, Moving Averages, and ATR-14.
- **`web/app.py`**: FastAPI backend with PBKDF2 authentication, AES-256 Fernet encrypted Gemini BYOK keys, and SSE streaming (`/api/scan/stream`).

---

## 3. Mandatory AI Model Invariants

- **Default Primary LLM**: `gemini-3.8-flash` (used for CIO synthesis, briefings, risk analysis, and deep dives).
- **Fast Sentiment Model**: `gemini-3.1-flash-lite` (batch comment sentiment classification; sub-second latency).
- **Guidelines ([`GEMINI.md`](GEMINI.md))**:
  1. **Transparent Fallbacks**: If an external service/model fails and degrades to fallback (e.g., regex), it must log the failure reason, surface a visible indicator (`classifier_mode="REGEX_FALLBACK"`), and give a 1-sentence heads-up in chat.
  2. **Live Endpoint Sanity Checks**: Passing mocked unit tests does NOT prove external endpoints or model names exist. Before declaring an external service or model change complete, run an un-mocked live probe.
  3. **Zero Cleartext Secrets**: Never echo, log, or pass API keys as literal strings in terminal commands or scripts. Silently import from `config.gemini_api_key`.

---

## 4. Development & Testing Commands

All commands run from the repository root:

```bash
# 1. Activate virtual environment
source .venv/bin/activate

# 2. Run automated test suite (Strict test database isolation; uses tmp_path)
.venv/bin/pytest

# 3. Run fast syntax and linting checks
.venv/bin/ruff check . --ignore E501

# 4. Run single test module
.venv/bin/pytest tests/test_temporal_briefings.py -v
```

> [!IMPORTANT]
> Always verify that `.venv/bin/pytest` reports **100% pass rate** before committing or deploying.

---

## 5. Production Upload & Deployment Playbook

### Prerequisites
- Google Cloud SDK (`gcloud`) authenticated to project `financial-sentinel-507007`.
- Cloud SDK Python environment: `/Users/frank/python311/bin/python3.11`.
- Terminal Sandbox: `git push`, Cloud Build, Cloud Run deployments, and external health curls require **`BypassSandbox: true`**.

### Standard Deployment Workflow

```bash
# Step 1: Ensure workspace is clean and tests pass
.venv/bin/pytest
.venv/bin/ruff check . --ignore E501

# Step 2: Commit changes to Git
git add -A
git commit -m "feat/fix: descriptive summary of changes"

# Step 3: Push to GitHub remote (Requires network / BypassSandbox: true)
git push origin main

# Step 4: Deploy to Google Cloud Run (Requires BypassSandbox: true)
./deploy/deploy_gcp.sh
```

### What `./deploy/deploy_gcp.sh` Executes Under the Hood

1. **Build Container Image**:
   ```bash
   gcloud builds submit --tag "gcr.io/financial-sentinel-507007/financial-sentinel" .
   ```
2. **Deploy Container to Cloud Run**:
   ```bash
   gcloud run deploy "financial-sentinel" \
       --image "gcr.io/financial-sentinel-507007/financial-sentinel" \
       --platform managed \
       --region "us-central1" \
       --allow-unauthenticated \
       --max-instances=1 \
       --no-cpu-throttling \
       --memory 1Gi \
       --cpu 1 \
       --set-env-vars "DASHBOARD_AUTH_ENABLED=true,SERVICE_URL=https://financial-sentinel-272533633552.us-central1.run.app,GEMINI_MODEL=gemini-3.8-flash,TELEGRAM_ALLOWED_USERNAMES=forello0,TELEGRAM_CHAT_ID=5251594125,GCS_DATA_BUCKET=financial-sentinel-data-507007,GCS_SYNC_ENABLED=true" \
       --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,DASHBOARD_PASSWORD=dashboard-password:latest,APP_SECRET_KEY=app-secret-key:latest,CRON_SECRET=cron-secret:latest"
   ```

### Step 5: Post-Deploy Verification

Verify that the live endpoint is responsive and SQLite connected:

```bash
curl -s https://financial-sentinel-272533633552.us-central1.run.app/health
```

Expected JSON response:
```json
{"status":"healthy","database":"connected","scheduler":"stopped","version":"2.4.0"}
```
