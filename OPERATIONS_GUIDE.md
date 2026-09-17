# 🛡️ Financial Sentinel: Master Developer & Operational Handbook

> **System Version:** `2.4.0` | **Target Model:** Gemini 3.8 Flash | **Active Platform:** Google Cloud Run (`us-central1`)  
> **Repository Root:** `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`  
> **Production Service:** `https://financial-sentinel-272533633552.us-central1.run.app`  
> **Security Audit Reference:** [`AUDIT_DOSSIER.md`](AUDIT_DOSSIER.md)

---

## 📑 Table of Contents
1. [Core Mission & The 5 Foundational Doctrines](#1-core-mission--the-5-foundational-doctrines)
2. [Chronological Engineering Arc & Hard-Fought Breakthroughs](#2-chronological-engineering-arc--hard-fought-breakthroughs)
3. [System Architecture & Multi-Agent Pipeline](#3-system-architecture--multi-agent-pipeline)
4. [Quantitative Engine & Deterministic Indicator Invariants](#4-quantitative-engine--deterministic-indicator-invariants)
5. [Adversarial Critic & The Anti-Rubberstamp Grading System](#5-adversarial-critic--the-anti-rubberstamp-grading-system)
6. [Temporal Grounding & The Anti-Hallucination Framework](#6-temporal-grounding--the-anti-hallucination-framework)
7. [Persistence & Data Durability (Dual-Layer SQLite + GCS Sync)](#7-persistence--data-durability-dual-layer-sqlite--gcs-sync)
8. [Security & Cryptographic Invariants (Audit Remediation Matrix)](#8-security--cryptographic-invariants-audit-remediation-matrix)
9. [Web Console, SSE Telemetry & UI Reactivity Patterns](#9-web-console-sse-telemetry--ui-reactivity-patterns)
10. [Telegram Terminal Bot & Webhook Cryptography](#10-telegram-terminal-bot--webhook-cryptography)
11. [Containerization, Dockerfile & `.dockerignore` Perimeter](#11-containerization-dockerfile--dockerignore-perimeter)
12. [Git Hygiene & Test Database Isolation](#12-git-hygiene--test-database-isolation)
13. [Google Cloud Run Deployment Playbook](#13-google-cloud-run-deployment-playbook)
14. [Environment Variables & Secret Manager Mappings](#14-environment-variables--secret-manager-mappings)
15. [Master Troubleshooting Matrix (Lessons Learned)](#15-master-troubleshooting-matrix-lessons-learned)
16. [Comprehensive Checklist for New Agents & Engineers](#16-comprehensive-checklist-for-new-agents--engineers)

---

## 1. Core Mission & The 5 Foundational Doctrines

Financial Sentinel is an autonomous investment intelligence, quantitative risk governance, and multi-agent synthesis platform. Across its development lifecycle, five non-negotiable engineering doctrines were established:

### Doctrine 1: The "Zero Mock Data / No Static Fallback" Doctrine
* Early prototypes had static `$100.00` mock fallbacks or cached quotes that masked live API failures (e.g. on tickers like `VOO`, `MU`, `SFY`).
* **The Iron Rule:** **Never mask external API failures with fictional or static numbers.** If Robinhood or Yahoo Finance fails to deliver a live quote, the system must explicitly notify the user, surface the failed ticker symbol, and refuse to calculate false PnL based on made-up prices.

### Doctrine 2: Deterministic Math Over LLM Hallucinations
* LLMs must **never** calculate technical indicators (RSI-14, MACD, Bollinger Bands, Moving Average spreads), portfolio Beta, Value-at-Risk (VaR), or position sizing.
* All mathematical metrics are computed deterministically in Python numpy/scipy routines. The LLM receives pre-calculated numbers and is confined strictly to qualitative synthesis, thesis stress-testing, and catalytic reasoning.

### Doctrine 3: The "Anti-Rubberstamp" Adversarial Hurdle
* The system rejects sycophantic AI agreement. In early versions, the Critic Agent gave an effortless "B+" to every opportunity.
* The Adversarial Critic is engineered to be genuinely skeptical: it assigns aggressive counter-theses, constructs "Devil's Advocate" objection boxes, exposes balance sheet or supply chain vulnerabilities, and enforces mathematical risk/reward confidence hurdles with differentiated grades (`A`, `B`, `C`, `D`, `F`).

### Doctrine 4: Institutional Depth Over Superficial AI Fluff
* Briefings and deep dives must read like rigorous, high-conviction Chief Investment Officer (CIO) investment memos—dense, data-grounded, multi-sentence paragraphs.
* Puffed-up buzzwords, empty rhetorical profundity, and superficial 3-word bullet points are strictly forbidden by prompt system instructions.

### Doctrine 5: Strict Temporal Grounding
* The system rejects relative date hallucinations (e.g. describing a Fed decision that concluded today as "happening tomorrow").
* All agent generation runs must pass `as_of=now_pst`, format news headlines with relative publication age (`[Today 02:00 PM EDT (4.0h ago)]`), and inject official macroeconomic calendar ground truth into every prompt.

---

## 2. Chronological Engineering Arc & Hard-Fought Breakthroughs

Understanding the historical evolution prevents regression into past traps:

| Phase / Commit | Core Challenge Encountered | Breakthrough & Resolution |
| :--- | :--- | :--- |
| **Phase 1: Foundation (`v2.3.0`)** | Static mock data was silently overwriting real market prices; tickers like `VOO` and `MU` pulled bogus values. | Eliminated all `$100` static mock fallbacks. Integrated live Robinhood market quote APIs with Yahoo Finance fallback and explicit error reporting. |
| **Test DB Pollution (`1d23908`)** | Running `pytest` locally was writing to `storage/state.db`, wiping the user's active portfolio holdings and reset cash to defaults. | Strict test database isolation: created isolated `tmp_path` fixtures in pytest so test suites never touch production SQLite files. |
| **Telegram Webhook Desync (`137d79d`)** | Telegram bot failed to respond to commands on Cloud Run due to 403 Forbidden errors on webhook callbacks. | Harmonized resolved webhook secret tokens between the registration setup call and incoming request validation middleware. |
| **GFE Edge Routing (`5e2f156`)** | Google Frontend (GFE) edge proxies on Cloud Run collided with standard `/health` probes. | Implemented redundant aliasing across `/health`, `/api/health`, and `/healthz` with lightweight JSON heartbeat. |
| **Starlette Template Breakage (`a83033d`)** | Starlette updated `TemplateResponse` to require explicit keyword arguments (`request=request`), breaking login views. | Refactored all web template controllers to adhere to modern Starlette/FastAPI keyword argument signatures. |
| **Security Audit Overhaul (`a3e5f20`)** | External AI audit surfaced 11 critical architectural risks (BYOK key exposure, plaintext passwords, indefinite sessions). | Implemented AES-256 Fernet BYOK encryption, PBKDF2 hashing, token epochs, parameterized SQL, and CSP security headers. |
| **Temporal Lag Defect (`7bcfd3e`)** | September 16, 2026 post-market report described the Fed rate decision as "happening tomorrow" even though it finished earlier today. | Built the Deterministic Macroeconomic Calendar engine; injected real-time session ground truth and age-tagged news headlines. |
| **Cloud Run PORT Collision (`ab34294`)** | `gcloud run deploy` crashed with exit code 1 when `--set-env-vars PORT=8000` was specified. | Discovered Cloud Run reserves `PORT` as a system variable. Removed `PORT` from deployment flags while configuring Uvicorn to listen on `${PORT:-8080}`. |
| **Sub-15ms Reactivity Defect (`4e280a5`)** | Clicking "Refresh Macro Calendar" gave zero visual feedback because in-memory execution took 10ms and rendered identical DOM. | Added active Tailwind spinning loader, minimum 400ms perceptual delay, container dimming, "Last Synced" clock badge, and top toast banner. |

---

## 3. System Architecture & Multi-Agent Pipeline

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

### Agent Roles & Source Files:
1. **`NewsIngestionAgent` ([`agents/news_ingestion.py`](agents/news_ingestion.py)):** Ingests RSS feeds, financial wires, and SEC filings. Chronologically sorts articles by `published_at DESC` and persists them into SQLite.
2. **`PortfolioRiskAgent` ([`agents/portfolio_risk.py`](agents/portfolio_risk.py)):** Analyzes asset correlation, sector concentration, portfolio beta, supply chain ripple vulnerabilities, and parametric Value-at-Risk.
3. **`OpportunityHunterAgent` ([`agents/opportunity_hunter.py`](agents/opportunity_hunter.py)):** Scans for asymmetric risk/reward setups, thematic alpha, and moonshot opportunities with clear invalidation stop-loss levels. Runs concurrently with the Risk Agent.
4. **`AdversarialCriticAgent` ([`agents/adversarial_critic.py`](agents/adversarial_critic.py)):** Evaluates theses generated by the Opportunity Hunter, checks valuation multiples against reality, assigns Devil's Advocate objections, and calculates a graded confidence score.
5. **`CIOSynthesisAgent` ([`agents/cio_synthesis.py`](agents/cio_synthesis.py)):** Compiles unified executive intelligence under the supervision of `TokenBudgetGuard` ([`analytics/token_budget.py`](analytics/token_budget.py)) to prevent prompt blowup.

### Decoupled Domain Services ([`services/`](services/)):
To prevent presentation channels (FastAPI / Telegram) from executing direct SQL mutations or business logic, operations are routed through domain services:
* **`IdentityService` ([`services/identity_service.py`](services/identity_service.py)):** Session authentication, PBKDF2 verification, BYOK key encryption/decryption, and token epoch rotation.
* **`PortfolioService` ([`services/portfolio_service.py`](services/portfolio_service.py)):** Position mutations, weighted average cost basis, liquid cash adjustments, and portfolio valuations.
* **`AnalysisService` ([`services/analysis_service.py`](services/analysis_service.py)):** Concurrency coordination across technicals, sentiment streams, and deep-dive archiving.
* **`BriefingService` ([`services/briefing_service.py`](services/briefing_service.py)):** Orchestrates scheduled daily briefings, GCS persistence, and Telegram push dispatches.

---

## 4. Quantitative Engine & Deterministic Indicator Invariants

### 1. Mathematical Technical Momentum ([`analytics/technical_indicators.py`](analytics/technical_indicators.py))
* **RSI-14:** Computed using Wilder's exponential smoothing on 14-period close price gains and losses. Identifies oversold ($\le 30$) and overbought ($\ge 70$) regimes.
* **MACD (12, 26, 9):** Fast 12-period EMA minus slow 26-period EMA, paired with a 9-period signal EMA line and divergence histogram.
* **Bollinger Bands (20, 2):** 20-period SMA $\pm 2$ standard deviations, outputting upper band, lower band, and `%B` bandwidth penetration.
* **Moving Average Spread:** Distance percentage from verified 50-day and 200-day simple moving averages to detect golden/death crosses.

### 2. Retail Sentiment Velocity & RVOL ([`analytics/sentiment_stream.py`](analytics/sentiment_stream.py))
* **Message Arrival Velocity:** Calculates true message arrival density ($\text{messages}/\text{hour}$) using true timestamp deltas ($t_{\text{first}} - t_{\text{last}}$) rather than naive raw message counts.
* **Relative Volume (RVOL):** Evaluates today's trading volume against the rolling 20-day historical trading volume average where $\text{Volume} > 0$.
* **Bayesian Laplace Smoothing:** Adjusts raw bullish/bearish message counts against a 50% neutral prior to eliminate retail call-skew distortion on low-volume symbols.

### 3. Quantitative Risk Modeling ([`analytics/quant_risk.py`](analytics/quant_risk.py))
* **Parametric & Historical VaR:** Calculates 95% and 99% 1-day Value-at-Risk across portfolio holdings.
* **Portfolio Beta:** Regresses portfolio holdings against SPY historical daily returns.
* **Herfindahl-Hirschman Index (HHI):** Measures portfolio concentration ($\sum w_i^2$). Flagged as high risk when $\text{HHI} > 0.25$.
* **Half-Kelly Position Sizing:** Recommends optimal capital allocation using fractional Kelly criteria ($f^* = \frac{p \cdot b - q}{b} \cdot 0.5$) with downside capital preservation limits.

---

## 5. Adversarial Critic & The Anti-Rubberstamp Grading System

The `AdversarialCriticAgent` ([`agents/adversarial_critic.py`](agents/adversarial_critic.py)) acts as the platform's Chief Risk Officer. It is explicitly programmed to counter confirmation bias:

1. **Structured Critique Schema:** Every candidate stock must return:
   * `reviewer_summary`: Detailed breakdown of vulnerabilities in the bull thesis.
   * `counter_thesis_questions`: Devil's Advocate inquiries detailing exact macroeconomic or microeconomic headwinds.
   * `downside_risk_threats`: Identifies margin compression, customer churn, supply shortages, or litigation risks.
   * `verdict`: Discrete grade (`A`, `B`, `C`, `D`, `F`).
   * `confidence_percentage`: 0% to 100% conviction score.
2. **Confidence Hurdle:** If confidence falls below 65%, the opportunity is flagged as speculative noise and denied promotion to executive briefings.

---

## 6. Temporal Grounding & The Anti-Hallucination Framework

To eliminate the "Fed rate announcement is tomorrow" temporal lag, the system enforces a 4-tier temporal architecture:

```
[ Current System Timestamp (America/Los_Angeles & America/New_York) ]
                           │
                           ▼
     ┌─────────────────────────────────────────────────────────────┐
     │ 1. Session Ground Truth Header Injection                    │
     │    "CURRENT TIME: Wednesday, Sep 16, 2026 | 03:00 PM PDT    │
     │     TRADING SESSION PHASE: Closed / Post-Market Wrap"       │
     └─────────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
     ┌─────────────────────────────────────────────────────────────┐
     │ 2. Deterministic Economic Calendar Ground Truth             │
     │    "TODAY'S CATALYSTS: 2:00 PM EDT FOMC Rate Decision       │
     │     STATUS: [COMPLETED - Concluded 5.2h ago at 02:00 PM]   │
     │     DIRECTIVE: Do NOT describe as upcoming or tomorrow."    │
     └─────────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
     ┌─────────────────────────────────────────────────────────────┐
     │ 3. Chronological Relative-Age News Tagging                  │
     │    "[Today 02:00 PM EDT (4.0h ago)] Fed raises rates 25bps  │
     │     [Yesterday Sep 15 (29.0h ago)] Fed meeting preview..."  │
     └─────────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
                 [ Gemini 3.8 Flash Synthesis Prompt ]
```

### Macroeconomic vs. Corporate Earnings Data Sources:
* **Deterministic Macro Calendar ([`analytics/economic_calendar.py`](analytics/economic_calendar.py)):**
  * Grounded in pre-compiled official schedules from the **Federal Reserve Board of Governors** (FOMC meetings 2025–2027), **Bureau of Labor Statistics** (CPI, PPI, Jobs/NFP), and **Bureau of Economic Analysis** (GDP).
  * Evaluated dynamically against `datetime.now()` to compute exact completion hours or countdowns.
* **Corporate Earnings Calendar ([`analytics/earnings_calendar.py`](analytics/earnings_calendar.py)):**
  * Live HTTP query to **Nasdaq's official API** (`https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`).
  * Extracts verified reporting dates, session timing (**BMO** vs. **AMC**), consensus EPS forecasts, and market caps.

---

## 7. Persistence & Data Durability (Dual-Layer SQLite + GCS Sync)

### SQLite Engine Settings:
* Local path: `storage/state.db`
* Configured on connection:
  ```python
  conn.execute("PRAGMA journal_mode=WAL;")
  conn.execute("PRAGMA synchronous=NORMAL;")
  conn.execute("PRAGMA foreign_keys=ON;")
  ```

### GCS Replication Protocol:
1. **Startup (`download_from_gcs`):** Downloads `state.db` from `gs://financial-sentinel-data-507007` upon container initialization. If absent, creates the schema and default admin `forello0`.
2. **Mutation Backup (`backup_to_gcs`):**
   * Uses SQLite's online backup API:
     ```python
     with sqlite3.connect(temp_backup_file) as backup_conn:
         conn.backup(backup_conn)
     ```
   * Uploads the clean backup to GCS. **Never copy raw WAL files directly, as this causes read tearing.**
3. **The Single-Writer Lock (`--max-instances=1`):**
   * SQLite is an embedded database. Multiple Cloud Run instances would write to GCS independently, creating split-brain data corruption. Max scale must remain 1.

---

## 8. Security & Cryptographic Invariants (Audit Remediation Matrix)

Documented in [`AUDIT_DOSSIER.md`](AUDIT_DOSSIER.md), Financial Sentinel adheres to 11 critical audit remediations:

| Finding | Risk | Mandatory Implementation |
| :--- | :--- | :--- |
| **C-1** | Multi-Tenant BYOK Leakage | User Gemini API keys are encrypted at rest using AES-256 Fernet backed by `APP_SECRET_KEY`. Keys are decrypted only in memory during LLM execution. |
| **C-2** | Plaintext Credentials | Passwords hashed using PBKDF2-HMAC-SHA256 with 100,000 rounds and unique salt. |
| **C-3** | Indefinite Sessions | User records have a `token_epoch` integer. Incrementing this immediately revokes all active session cookies across all devices. |
| **C-4** | Privilege Escalation | Explicit Role-Based Access Control (`role = 'admin'` vs `'user'`). Users cannot view other tenants' portfolios or deep dives. |
| **C-5** | SQL Injection | 100% Parameterized queries across `storage/state_store.py`. Zero SQL string formatting. |
| **C-6** | Path Traversal | `validate_safe_path()` confines all disk operations strictly inside `/app/data` or `/app/storage`. |
| **C-7** | Brute-Force DoS | IP-based sliding window rate limiter on login with periodic stale-IP memory cleanup. |
| **C-8** | Insecure Cookies | Session cookies enforce `HttpOnly=True`, `SameSite="lax"`, `Secure=True` on HTTPS, and 7-day expiration. |
| **C-9** | Security Headers | Middleware injects strict Content Security Policy (CSP), HSTS, `X-Frame-Options: DENY`, and nosniff. |
| **C-10** | WAL Read Tearing | GCS backup creates clean temporary snapshots via `conn.backup()`. |
| **C-11** | Hardcoded Defaults | `rotate_legacy_admin_credentials()` automatically rotates default `sentinel_admin` credentials on container boot. |

---

## 9. Web Console, SSE Telemetry & UI Reactivity Patterns

The web dashboard ([`web/app.py`](web/app.py)) is a 6-tab institutional console:
* **Tab 1:** Portfolio Overview & Holdings Management
* **Tab 2:** Position Risk & Quantitative Analytics
* **Tab 3:** Opportunity Hunter & Asymmetric Radar
* **Tab 4:** Ticker Deep Dive Studio
* **Tab 5:** Earnings & Macro Calendar (with live SSE/fetch, reactive spinner, last-synced badge)
* **Tab 6:** Executive Market Briefings Archive & Reader

### The Sub-15ms Reactivity Rule:
* In-memory API calls resolve in $<15\text{ ms}$. If the DOM updates with identical data, zero pixels visibly change on a 60Hz display.
* **All manual refresh buttons must enforce:**
  1. `disabled = true` and `animate-spin` on click.
  2. A minimum 400ms perceptual delay (`Promise.all([fetch, delay])`) so human perception registers the sync cycle.
  3. A visible `● Synced HH:MM:SS` timestamp badge.
  4. Momentary container dimming (`opacity-60` $\rightarrow$ `opacity-100`) and top banner confirmation (`showBanner`).

---

## 10. Telegram Terminal Bot & Webhook Cryptography

The Telegram bot ([`agents/telegram_bot.py`](agents/telegram_bot.py)) serves as a mobile command terminal:
* **Shorthand Commands:**
  * `/<ticker>`: Instant multi-agent deep dive with RSI, MACD, and Adversarial Critic grade.
  * `/portfolio`: Real-time portfolio holdings, market value, and unrealized PnL.
  * `/cash`: View or adjust liquid cash reserves.
  * `/briefing`: On-demand synthesis of latest market intelligence wrap.
* **Webhook Security:**
  * Telegram sets webhook via `/setWebhook` with `secret_token=APP_SECRET_KEY[:32]`.
  * Incoming updates are validated against `X-Telegram-Bot-Api-Secret-Token`.
* **Access Control Whitelist:**
  * Only users matching `TELEGRAM_ALLOWED_USERNAMES` (e.g. `forello0`) can execute commands.

---

## 11. Containerization, Dockerfile & `.dockerignore` Perimeter

### Key Container Rules:
1. **Non-Root Execution:** Container runs strictly as `USER sentinel` (UID 1000). Never execute container processes as root.
2. **Dynamic `$PORT`:** Cloud Run injects `$PORT` (typically 8080). Uvicorn must listen on `0.0.0.0:${PORT:-8080}`.
3. **Pre-allocated Storage Paths:** `/app/storage` (SQLite) and `/app/data` are created and chowned to `sentinel` during image build.

---

## 12. Git Hygiene & Test Database Isolation

### Test Suite Isolation Protocol:
> [!IMPORTANT]
> Running tests must **never** touch the production or local database files. In `tests/conftest.py` and test modules, all `StateStore` fixtures use `tmp_path / "test.db"`. This guarantees unit tests never overwrite active portfolio holdings or cash reserves.

### Testing Invariant:
* Run `.venv/bin/pytest` prior to any commit.
* **Current Baseline:** 127 passed, 0 failures.

---

## 13. Google Cloud Run Deployment Playbook

### Step 1: Container Build via Google Cloud Build
Run outside the sandbox (`BypassSandbox: true`):
```bash
CLOUDSDK_PYTHON=/Users/frank/python311/bin/python3 \
CLOUDSDK_METRICS_ENVIRONMENT="${CLOUDSDK_METRICS_ENVIRONMENT:+$CLOUDSDK_METRICS_ENVIRONMENT }datacloud.antigravity" \
/Users/frank/google-cloud-sdk/bin/gcloud builds submit \
  --tag "gcr.io/financial-sentinel-507007/financial-sentinel" . \
  --project=financial-sentinel-507007
```

### Step 2: Rollout to Google Cloud Run
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
*(Or execute `./deploy/deploy_gcp.sh`)*

---

## 14. Environment Variables & Secret Manager Mappings

| Variable / Secret | Type | Value / Source | Purpose |
| :--- | :--- | :--- | :--- |
| `DASHBOARD_AUTH_ENABLED` | Env Var | `true` | Enforces login session cookie verification |
| `SERVICE_URL` | Env Var | `https://financial-sentinel-...` | Base URL for webhooks and OAuth redirects |
| `GEMINI_MODEL` | Env Var | `gemini-3.8-flash` | Primary reasoning model |
| `TELEGRAM_ALLOWED_USERNAMES` | Env Var | `forello0` | Whitelist for Telegram bot execution |
| `TELEGRAM_CHAT_ID` | Env Var | `5251594125` | Push destination for automated briefings |
| `GCS_DATA_BUCKET` | Env Var | `financial-sentinel-data-507007` | Cloud Storage bucket for database snapshots |
| `GCS_SYNC_ENABLED` | Env Var | `true` | Enables periodic & on-mutation GCS replication |
| `GEMINI_API_KEY` | Secret | `gemini-api-key:latest` | System Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Secret | `telegram-bot-token:latest` | Telegram Bot API token |
| `DASHBOARD_PASSWORD` | Secret | `dashboard-password:latest` | Admin dashboard password |
| `APP_SECRET_KEY` | Secret | `app-secret-key:latest` | Master key for AES-256 BYOK encryption & session cookies |
| `CRON_SECRET` | Secret | `cron-secret:latest` | Bearer token authenticating Cloud Scheduler triggers |

---

## 15. Master Troubleshooting Matrix (Lessons Learned)

| Symptom / Trap | Root Cause | Permanent Resolution |
| :--- | :--- | :--- |
| **Cloud Run deploy fails with `reserved env names: PORT`** | `PORT` was passed in `--set-env-vars`. | Never pass `PORT` in deployment flags. Cloud Run sets it automatically. |
| **Briefings freeze or miss scheduled run times** | Cloud Run suspended CPU because `--no-cpu-throttling` was omitted. | Always pass `--no-cpu-throttling` on Cloud Run. |
| **Split-brain database corruption in GCS** | Cloud Run scaled to $>1$ instance, causing concurrent overwrite of `state.db`. | Always enforce `--max-instances=1`. |
| **Running pytest wiped active user portfolio** | Tests wrote to production database `storage/state.db`. | Always use `tmp_path` fixture in pytest to isolate test state. |
| **UI refresh button looks dead / unresponsive** | Backend responded in 10ms with identical data; no visible pixel changed on screen. | Add spinner, minimum 400ms delay, container opacity dimming, and "Last Synced" timestamp. |
| **Telegram bot returns 403 Forbidden on webhooks** | Secret token used during `/setWebhook` did not match verification header. | Use synchronized `APP_SECRET_KEY[:32]` for both setup and verification. |
| **Post-market wrap describes today's Fed meeting as "tomorrow"** | Prompt lacked calendar date grounding and news headlines lacked relative age tags. | Enforce Rule 3 in `BRIEFING_COMMUNICATION_RULES`, inject economic calendar, and stamp news with relative age. |

---

## 16. Comprehensive Checklist for New Agents & Engineers

When initiating a new session or feature branch:
1. [ ] **Verify Working Directory:** Confirm Cwd is `/Users/frank/.gemini/antigravity/scratch/financial_agent_system`.
2. [ ] **Run Baseline Tests:** Run `.venv/bin/pytest` and verify 127/127 tests pass before modifying any code.
3. [ ] **Adhere to Zero-Mock Doctrine:** Never substitute fictional prices, mock returns, or synthetic indicators.
4. [ ] **Preserve Single-Writer Lock:** Never increase Cloud Run `--max-instances` above 1 without migrating SQLite to Cloud SQL.
5. [ ] **Ground All Briefing Prompts:** Always supply `as_of=now_pst` and inject `economic_calendar` ground truth.
6. [ ] **Maintain Threat Model Hygiene:** Parameterized SQL queries only; never interpolate raw strings into database calls.
7. [ ] **Deploy Safely:** Ensure all tests pass, commit to git with conventional commit messages, and execute the 2-step Cloud Build + Cloud Run deployment.
