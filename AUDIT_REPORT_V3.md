# Comprehensive Adversarial System Audit: Financial Sentinel V3

## 1. Executive Health Scorecard

| Pillar | Score (1-10) | Status | Assessment Summary |
| :--- | :---: | :--- | :--- |
| **1. Fail Loudly & Data Provenance** | **6/10** | ⚠️ At Risk | Found lingering synthetic defaults in market overview fetches suppressing explicit failure tags. |
| **2. Concurrency & DB Integrity** | **5/10** | 🚨 Critical | High risk of permanent data loss due to a GCS Sync race condition where a container restart can overwrite remote state with an empty local DB. |
| **3. Async Event Loop Hygiene** | **4/10** | 🚨 Critical | The main FastAPI event loop is heavily blocked by synchronous SQLite reads in the core authentication dependency. |
| **4. Multi-Tenant Security & Injection** | **7/10** | ⚠️ At Risk | The prompt injection defense relies on malformed XML/SGML tags that fail to properly isolate untrusted headlines from system instructions. |
| **5. Prompt Architecture & Tokens** | **7/10** | ⚠️ At Risk | System instructions and dynamic data are intertwined, preventing optimal Vertex/Gemini prefix caching. |
| **6. Operational Realities & Lifecycle** | **4/10** | 🚨 Critical | Cloud Run SIGTERM does not checkpoint SQLite WAL to GCS resulting in data loss. Briefing schedules use PST static hours, breaking relative to the EST market bell during DST shifts. |

---

## 2. Prioritized Vulnerability Register

### 🔴 P0 Critical: GCS Sync Race Condition (Data Loss)
* **File:** `storage/state_store.py` (Lines ~198-251)
* **Mechanism:** In `restore_from_gcs()`, if the remote `state.db` blob does not exist (e.g., due to eventual consistency or initial setup), `_GCS_REMOTE_GENERATION` is set to `None`. Subsequently, `backup_to_gcs()` detects `None` and uploads the file **without** the `if_generation_match` precondition. A rapidly restarting container that fails to read the blob can therefore overwrite the entire remote GCS database with an uninitialized local database.
* **Solution:** If `_GCS_REMOTE_GENERATION` is `None`, the backup process must explicitly pass `if_generation_match=0` to ensure the upload only succeeds if no remote blob currently exists, preventing catastrophic overwrites of established databases.

### 🔴 P0 Critical: Main Event Loop Blocking (Async Hygiene)
* **File:** `web/auth_deps.py` (Line ~48)
* **Mechanism:** `get_current_user_optional()` performs a direct, synchronous SQLite read (`store.get_user_by_id(payload["uid"])`) without `await asyncio.to_thread()`. Because this dependency is injected via `@app.middleware("http")` and `Depends(require_user)`, every single protected API request blocks the main asyncio event loop, severely degrading concurrent request throughput.
* **Solution:** Refactor the middleware and dependency injection to await a dedicated asynchronous database wrapper, ensuring `store.get_user_by_id` runs inside the `sentinel_executor` thread pool.

### 🔴 P0 Critical: Unsafe SIGTERM Termination (Data Loss)
* **File:** `web/app.py` (Lines ~139-152)
* **Mechanism:** When Cloud Run sends a `SIGTERM` signal, the `lifespan` teardown block gracefully shuts down the scheduler and thread pools but **completely omits** flushing the SQLite WAL or forcing a synchronous `backup_to_gcs(blocking=True)`. Any state changes made right before termination are permanently lost when the ephemeral container is destroyed.
* **Solution:** Add explicit lifecycle teardown hooks in `lifespan`: `orchestrator.state_store.close_connection()`, a synchronous WAL checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`), and `orchestrator.state_store.backup_to_gcs(blocking=True)`.

### 🟠 P1 High: Unpooled HTTP Connections (Latency)
* **File:** `agents/news_ingestion.py` (Lines ~444-445)
* **Mechanism:** `fetch_live_feed` calls `httpx.get(url, ...)` instead of utilizing a persistent HTTP/2 `httpx.Client`. Every RSS feed polling cycle creates dozens of new TLS connections and TCP handshakes from scratch, wasting hundreds of milliseconds of latency per request.
* **Solution:** Instantiate and utilize the pooled `get_market_http_client()` from `analytics/market_data.py` (or a dedicated news client pool) to leverage HTTP/2 multiplexing and connection keep-alive.

### 🟠 P1 High: Prompt Injection Tag Mismatch
* **File:** `agents/opportunity_agent.py` (Lines ~155, 31)
* **Mechanism:** The prompt injection defense wraps headlines in `<<<UNTRUSTED_HEADLINE source="...">>>`. However, it closes the tag with an identical `<<<UNTRUSTED_HEADLINE>>>` rather than a proper `<<</UNTRUSTED_HEADLINE>>>` closing delimiter. The system instructions tell the LLM to look for `<<<UNTRUSTED_HEADLINE>>>`, causing the LLM to fail to identify the bounds of the unverified content, leaving it vulnerable to malicious command execution embedded in clickbait headlines.
* **Solution:** Ensure symmetrical and semantically valid XML-style boundaries. Open with `<UNTRUSTED_HEADLINE source="...">` and strictly close with `</UNTRUSTED_HEADLINE>`, updating the system instructions to match.

### 🟡 P2 Medium: Hardcoded Timezone Drift (DST Inaccuracy)
* **File:** `scheduler.py` (Lines ~274-297)
* **Mechanism:** The scheduler strictly triggers events based on fixed `America/Los_Angeles` wall-clock hours (e.g., 6:30 AM PST, 10:00 AM PST). Because DST shifts can occasionally cause edge-case misalignment, alerts may drift relative to the actual NYSE opening bell (9:30 AM `America/New_York`).
* **Solution:** The scheduler logic should be anchored to `America/New_York` (EST/EDT) explicitly mapping to 9:30 AM, 1:00 PM, and 4:00 PM market events rather than relying on Pacific Time conversions.

### 🟡 P2 Medium: Synthetic Defaults Swallowing Errors
* **File:** `analytics/market_data.py` (Line ~404)
* **Mechanism:** `fetch_market_overview()` catches exceptions on benchmark quote fetching and silently returns `{"current_price": 0.0, "change_pct": 0.0}`. This violates the fail-loudly directive. The UI/LLM will interpret the SPY index as having crashed 100% to $0.00 rather than understanding the upstream API timed out.
* **Solution:** Remove the synthetic fallback. Propagate the error and explicitly populate `fields_unavailable` in the `PortfolioStressMetric` or market overview response, triggering degradation warnings.

---

## 3. Immediate Action Plan

1. **Fix Data Integrity First:** Update `storage/state_store.py` to pass `if_generation_match=0` when `_GCS_REMOTE_GENERATION` is `None` to prevent accidental empty state overwrites. Add `backup_to_gcs(blocking=True)` and WAL checkpointing to the `web/app.py` SIGTERM lifecycle teardown.
2. **Restore Event Loop Throughput:** Decouple `get_current_user_optional` from synchronous SQLite queries. Wrap the DB fetch in `await asyncio.to_thread()` and refactor the auth dependency tree to handle it asynchronously.
3. **Patch Prompt Injection & Latency:** Fix the mismatched `<<<UNTRUSTED_HEADLINE>>>` tags in the Opportunity Agent and migrate the News Ingestion Agent to use the centralized, persistent HTTP/2 connection pool.
4. **Remove Bandaids:** Eliminate the `0.0` fallback in `fetch_market_overview` and the `float('nan')` masking in `compute_daily_log_returns`. Force the system to fail loudly and downgrade gracefully.
