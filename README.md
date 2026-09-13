# 🛡️ Financial Sentinel & Alpha Discovery Multi-Agent Platform

[![Version](https://img.shields.io/badge/version-2.3.0-blue.svg)](config.py)
[![Tests](https://img.shields.io/badge/tests-68%20passed-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](requirements.txt)
[![GCP Cloud Run](https://img.shields.io/badge/deployment-Cloud%20Run-orange.svg)](Dockerfile)
[![Security](https://img.shields.io/badge/security-AES--256%20BYOK%20%7C%20Secret%20Manager-purple.svg)](auth/crypto.py)

An autonomous, multi-agent financial intelligence and risk governance system combining **defensive portfolio monitoring**, **deterministic technical momentum**, **retail social sentiment velocity**, and **offensive asymmetric opportunity discovery**.

---

## 🏛️ System Architecture

```
                                [ Live Market Feeds ]
         (Robinhood Quotes, Yahoo Finance Bars, StockTwits Streams, SEC 8-K, CNBC, MarketWatch)
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
      │    (Supply Chain Ripple,  │                 │    (Thematic Alpha,       │
      │     Quant VaR, Beta, HHI) │                 │     Moonshot Discovery)   │
      └─────────────┬─────────────┘                 └─────────────┬─────────────┘
                    │                                             │
                    └─────────────────────┬───────────────────────┘
                                          ▼
                              ┌───────────────────────────┐
                              │ 4. Adversarial Critic     │
                              │    (Stress-Test Theses,   │
                              │     Confidence Hurdle)    │
                              └───────────┬───────────────┘
                                          ▼
                              ┌───────────────────────────┐
                              │ 5. CIO Synthesis Agent    │
                              │    (Gemini 3.8 Flash,     │
                              │     Token Budget Guard)   │
                              └───────────┬───────────────┘
                                          │
          ┌───────────────────────────────┴───────────────────────────────┐
          ▼                                                               ▼
┌───────────────────────────┐                                   ┌───────────────────────────┐
│ Web Console (FastAPI)     │                                   │ Telegram Terminal Bot     │
│ • Live SSE Telemetry      │                                   │ • Shorthand /<ticker>     │
│ • Deep Dive Archive       │                                   │ • /portfolio, /cash       │
│ • Market Briefings Reader │                                   │ • Automated Daily Crons   │
└───────────────────────────┘                                   └───────────────────────────┘
```

---

## 🚀 Key Modules & Capabilities

### 1. Deterministic Technical Momentum Engine (`analytics/technical_indicators.py`)
* Computes pure mathematical indicators without LLM approximation:
  * **RSI-14:** Wilder's smoothed momentum with oversold (<= 30) and overbought (>= 70) regimes.
  * **MACD (12, 26, 9):** Fast/slow exponential moving averages, signal line, and divergence histogram.
  * **Moving Average Spread:** Distance from verified 50-day and 200-day simple moving averages.
  * **Bollinger Bands (20, 2):** Upper, lower, and %B band penetration metrics.
* Fetches historical bars directly from Robinhood market data APIs with Yahoo Finance fallback.

### 2. Grounded Retail Sentiment & Message Velocity (`analytics/sentiment_stream.py`)
* Analyzes live StockTwits public discussion streams.
* Calculates **Message Arrival Velocity** (`messages/hour`) using true timestamp spans (t_first - t_last) rather than arbitrary message counts.
* Computes **Historical Relative Volume (RVOL)** by comparing latest session volume against the prior 20-day trading average (Volume > 0).
* Applies Bayesian Laplace smoothing against a 50% neutral prior to prevent retail call-skew distortions.

### 3. Ticker Deep Dive Workspace & Archive (`web/static/js/deepdive.js`, `storage/state_store.py`)
* First-class single-ticker research console accessible via web or Telegram.
* Automatically records completed analyses into `user_deepdives` with extracted stances (`BULLISH`, `BEARISH`, `NEUTRAL`, `CAUTION`) and conviction scores.
* Replay past deep dives instantly from cache without re-querying the Gemini model.

### 4. Scheduled Market Intelligence Briefings (`scheduler.py`)
* Automated multi-cycle briefings aligned with Pacific Standard Time (PST/PDT):
  * **06:30 AM PST:** 🌅 Pre-Market Intelligence & Opening Catalysts
  * **10:00 AM PST:** ☀️ Mid-Market Momentum & Fed Pulse
  * **03:00 PM PST:** 🌙 Post-Market Earnings & Hot Movers Wrap
  * **09:00 PM PST (Sun):** 🌟 Weekend Macro & Week-Ahead Preview
  * **Earnings Outlook:** 7-day verified Nasdaq corporate earnings calendar cross-referenced against portfolio holdings.

### 5. Multi-Tenant Zero-Trust Security (`auth/crypto.py`, `storage/state_store.py`)
* **BYOK (Bring Your Own Key):** User Gemini API keys are encrypted at rest using AES-256 (Fernet) backed by a 256-bit cryptographically secure master key managed in Google Secret Manager.
* **Strict Tenant Isolation:** Portfolio data, deep dive archives, and private briefings enforce role-based access control (RBAC). Non-owners attempting cross-tenant access receive HTTP 403 Forbidden.
* **Out-of-Band Key Header Auth:** All calls to Google Generative Language API pass credentials via HTTP `x-goog-api-key` headers rather than URL query parameters to prevent log leakage.

### 6. Cloud Run Serverless Architecture
* **Single-Writer Concurrency Lock:** Service deployed with `--max-instances=1` (`autoscaling.knative.dev/maxScale: 1`), guaranteeing that exactly one container instance mounts SQLite and pushes snapshots to Google Cloud Storage (`gs://financial-sentinel-data-507007/state.db`).
* **Synchronous WAL Checkpointing:** SQLite transactions are flushed via `PRAGMA wal_checkpoint(TRUNCATE);` before GCS snapshot uploads.
* **Dedicated Worker Pool:** FastAPI event loop integrates a dedicated 16-worker `ThreadPoolExecutor` to eliminate thread starvation on single-core container runtimes.

---

## ⚡ Quick Start

### 1. Clone & Setup Environment
```bash
git clone <repository_url>
cd financial_agent_system

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables
```bash
cp .env.example .env
# Edit .env with your credentials (GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, APP_SECRET_KEY, etc.)
```

### 3. Run Automated Verification Suite
```bash
pytest tests/ -v
```
*Current test suite: **68 passed tests** covering multi-tenancy, encryption, technical indicators, sentiment streams, scheduler, and web APIs.*

### 4. Launch Web Dashboard
```bash
python web/app.py
```
Open **http://127.0.0.1:8000** in your browser. Default login: `admin` / `sentinel_admin` (or configured `DASHBOARD_PASSWORD`).

### 5. Run Interactive CLI
```bash
# Live scan of custom portfolio
python cli.py --portfolio data/sample_portfolio.json --live

# Interactive simulated breaking catalysts demo
python cli.py --demo
```

---

## 📱 Telegram Bot Commands

When configured with `TELEGRAM_BOT_TOKEN`, the bot acts as an interactive private terminal:

| Command | Action |
|---|---|
| `/<ticker>` or `$<ticker>` | Trigger single-ticker deep dive (e.g. `/NVDA`, `/AAPL`, ``) |
| `/portfolio` or `/stocks` | Live portfolio summary, equity valuation, and unrealized PnL |
| `/cash` | Deployable cash reserves and dry powder percentage |
| `/briefing` or `/today` | Generate/retrieve current daily market intelligence briefing |
| `/scan` | Execute multi-agent portfolio risk and alpha discovery scan |
| `/opportunity` | Top vetted asymmetric market opportunities |
| `/moonshots` | High-asymmetry deep tech alpha radar |
| `/status` | Terminal operational status and connected AI core check |
| `/help` | Complete interactive command menu |
| *Plain Text* | Routes directly to Gemini Lead Portfolio Manager conversational AI |

---

## 🔒 Configuration Reference (`.env`)

| Variable | Description | Required |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key for CIO synthesis & chat | Optional (BYOK supported) |
| `GEMINI_MODEL` | Target Gemini model (default: `gemini-3.8-flash`) | No |
| `APP_SECRET_KEY` | 256-bit random hex key for AES-256 BYOK encryption | Yes (production) |
| `DASHBOARD_AUTH_ENABLED` | Enable session authentication (`true`/`false`) | No (default: `false`) |
| `DASHBOARD_PASSWORD` | Master password for dashboard admin access | No |
| `TELEGRAM_BOT_TOKEN` | BotFather API token for Telegram channel | Optional |
| `TELEGRAM_ALLOWED_USERNAMES` | Comma-separated list of authorized Telegram handles | No |
| `DAILY_TOKEN_LIMIT` | Daily token budget ceiling (default: `500000`) | No |
| `GCS_DATA_BUCKET` | Cloud Storage bucket for persistent SQLite state snapshots | Cloud Run only |

---

## 🚢 Production Deployment

Deploy to Google Cloud Run with single-writer concurrency enforcement:

```bash
gcloud run deploy financial-sentinel   --source .   --region us-central1   --project <YOUR_GCP_PROJECT>   --max-instances=1   --set-secrets=GEMINI_API_KEY=gemini-api-key:latest   --set-secrets=TELEGRAM_BOT_TOKEN=telegram-bot-token:latest   --set-secrets=DASHBOARD_PASSWORD=dashboard-password:latest   --set-secrets=APP_SECRET_KEY=app-secret-key:latest
```

---

## 📄 License
Proprietary & Confidential. All rights reserved.
