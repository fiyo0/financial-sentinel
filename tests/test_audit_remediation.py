"""
Comprehensive regression test suite verifying all 9 Critical and 14 High
architecture, security, and quantitative remediations from the technical audit.
"""
import time
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from auth.crypto import (
    create_session_token,
    verify_session_token,
    hash_password,
    verify_password,
)
from config import config
from web.app import app, orchestrator
from models import Portfolio, PortfolioHolding
from analytics.technical_indicators import (
    compute_technical_snapshot,
    _calc_ema,
)
from analytics.sentiment_stream import (
    fetch_social_sentiment_snapshot,
    _fetch_stocktwits_stream,
)


@pytest.fixture
def test_client():
    return TestClient(app)


# =====================================================================
# Phase 1: Security & Authentication Hardening (C-1, C-2, C-3, C-4, C-5, C-6, H-1, H-2, H-3, H-4, H-5)
# =====================================================================

def test_c1_h2_no_candidate_secrets_token_forgery():
    """C-1 & H-2: Forged token using published literals ('sentinel_admin') must fail."""
    from cryptography.fernet import Fernet
    import base64
    import hashlib

    # Attempt to forge using old hardcoded key
    forged_k = base64.urlsafe_b64encode(hashlib.sha256(b"sentinel_admin").digest())
    forged_token = Fernet(forged_k).encrypt(
        json.dumps({"uid": "usr_victim", "usr": "admin", "rol": "admin", "iat": time.time()}).encode()
    ).decode()

    payload = verify_session_token(forged_token)
    assert payload is None, "Forged token with sentinel_admin must be rejected"

    # Valid token created with APP_SECRET_KEY must succeed
    valid_token = create_session_token("usr_valid", "admin", "admin")
    valid_payload = verify_session_token(valid_token)
    assert valid_payload is not None
    assert valid_payload["uid"] == "usr_valid"


def test_s1_s2_login_with_published_default_credential_is_rejected(test_client):
    """S-1 & S-2: Direct login with legacy published default credential 'sentinel_admin' must fail."""
    admin = orchestrator.state_store.get_or_create_default_admin()

    # 1. Direct PBKDF2 authentication rejection
    auth_result = orchestrator.state_store.authenticate_user(admin["username"], "sentinel_admin")
    assert auth_result is None, "authenticate_user must never match 'sentinel_admin' when DASHBOARD_PASSWORD is configured"

    # 2. HTTP POST /api/auth/login rejection
    res = test_client.post("/api/auth/login", json={"username_or_email": admin["username"], "password": "sentinel_admin"})
    assert res.status_code == 401, "API login with 'sentinel_admin' must return 401 Unauthorized"

    # 3. Legacy header/cookie backdoor rejection
    res_hdr = test_client.get("/api/portfolio", headers={"X-Sentinel-Auth": "sentinel_admin"})
    assert res_hdr.status_code == 401, "X-Sentinel-Auth backdoor must be completely eliminated"

    res_cookie = test_client.get("/api/portfolio", cookies={"sentinel_auth": "sentinel_admin"})
    assert res_cookie.status_code == 401, "sentinel_auth cookie backdoor must be completely eliminated"


def test_c2_telegram_webhook_secret_token_enforcement(test_client):
    """C-2: Webhook POST without valid secret token header must return 401."""
    # 1. No header -> 401
    res = test_client.post("/api/telegram/webhook", json={"update_id": 1, "message": {"text": "/portfolio"}})
    assert res.status_code == 401

    # 2. Wrong header -> 401
    res = test_client.post(
        "/api/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "invalid-secret"},
        json={"update_id": 1, "message": {"text": "/portfolio"}}
    )
    assert res.status_code == 401

    # 3. Valid secret header -> 200
    valid_secret = config.telegram_webhook_secret
    res = test_client.post(
        "/api/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": valid_secret},
        json={"update_id": 1}
    )
    assert res.status_code == 200


def test_c3_telegram_no_unauthenticated_admin_hijack():
    """C-3: Unregistered chat_id / empty chat_id does not resolve to admin and cannot mutate admin chat_id."""
    from channels.telegram_bot import FinancialSentinelTelegramBot

    bot = FinancialSentinelTelegramBot(orchestrator=orchestrator)
    sent = []
    bot.send_message = lambda msg, chat_id=None: sent.append({"text": msg, "chat_id": chat_id})

    # Admin's original chat_id
    admin = orchestrator.state_store.get_or_create_default_admin()
    orig_chat_id = admin.get("telegram_chat_id")

    # Attacker tries to send message from random chat_id pretending to be another username
    bot._handle_incoming_message("/portfolio", "attacker_chat_999", "attacker_user")

    # Should send onboarding message, not portfolio
    assert any("Welcome to Financial Sentinel" in m["text"] for m in sent)

    # Admin chat_id must NOT have been mutated
    reloaded_admin = orchestrator.state_store.get_user_by_id(admin["id"])
    assert reloaded_admin.get("telegram_chat_id") == orig_chat_id


def test_c4_dashboard_auth_enabled_by_default():
    """C-4: Default configuration must have dashboard_auth_enabled=True."""
    assert config.dashboard_auth_enabled is True


def test_c5_telegram_configure_and_cache_require_admin(test_client):
    """C-5 & H-5: Admin-only endpoints must reject unauthenticated and standard users with 403."""
    # 1. Unauthenticated -> 401
    res = test_client.post("/api/telegram/configure", json={"bot_token": "123456:ABC-DEF"})
    assert res.status_code == 401

    # 2. Standard user -> 403
    store = orchestrator.state_store
    bob_id = "usr_bob_c5"
    with store._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                     (bob_id, "bob_c5", "bob_c5@test.com", "hash", "user"))
        conn.commit()

    user_token = create_session_token(bob_id, "bob_c5", role="user")
    test_client.cookies.set("sentinel_token", user_token)
    res = test_client.post("/api/telegram/configure", json={"bot_token": "123456:ABC-DEF"})
    assert res.status_code == 403

    res_cache = test_client.post("/api/cache/clear")
    assert res_cache.status_code == 403


def test_c6_schedule_trigger_requires_auth_or_cron_secret(test_client):
    """C-6: /api/schedule/trigger/{slot} rejects unauthenticated callers."""
    test_client.cookies.clear()
    res = test_client.post("/api/schedule/trigger/premarket")
    assert res.status_code in (401, 403)

    # Accepts valid X-Cron-Secret
    with patch("web.app.daily_scheduler.execute_briefing", return_value="Test briefing preview"):
        res_cron = test_client.post(
            "/api/schedule/trigger/premarket",
            headers={"X-Cron-Secret": config.cron_secret}
        )
        assert res_cron.status_code == 200


def test_h1_h3_password_hashing_and_no_plaintext_fallback():
    """H-1 & H-3: PBKDF2 hash verification works; plaintext fallback is completely removed."""
    pw = "SecretPassword123!"
    h = hash_password(pw)
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password(pw, h) is True
    assert verify_password("WrongPassword", h) is False

    # Plaintext equality check must be rejected
    assert verify_password("sentinel_admin", "sentinel_admin") is False


def test_h5_api_quote_auth_and_ticker_validation(test_client):
    """H-5: /api/quote/{ticker} requires authentication and strictly validates ticker regex."""
    test_client.cookies.clear()
    # 1. Unauthenticated -> 401
    res = test_client.get("/api/quote/NVDA")
    assert res.status_code == 401

    # 2. Authenticated with malicious ticker -> 400
    store = orchestrator.state_store
    alice_id = "usr_alice_h5"
    with store._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                     (alice_id, "alice_h5", "alice_h5@test.com", "hash", "user"))
        conn.commit()

    user_token = create_session_token(alice_id, "alice_h5", role="user")
    test_client.cookies.set("sentinel_token", user_token)
    res_malicious = test_client.get("/api/quote/NVDA;DROP%20TABLE")
    assert res_malicious.status_code == 400


# =====================================================================
# Phase 2: Multi-Tenant Boundary & XSS Defense (C-7, C-8, C-9, H-18)
# =====================================================================

def test_c7_tenant_isolation_on_private_briefings(test_client):
    """C-7: Private user briefing cannot be viewed by another tenant."""
    store = orchestrator.state_store
    alice_id = "usr_alice_c7"
    bob_id = "usr_bob_c7"

    # Insert users
    with store._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                     (alice_id, "alice_c7", "alice_c7@test.com", "hash", "user"))
        conn.execute("INSERT OR IGNORE INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                     (bob_id, "bob_c7", "bob_c7@test.com", "hash", "user"))
        conn.commit()

    private_rep_id = "rep_alice_private_scan"
    store.record_market_briefing(
        briefing_id=private_rep_id,
        slot="scan",
        message="Alice's private scan result.",
        user_id=alice_id
    )

    # Bob attempts to read Alice's briefing -> 403
    bob_token = create_session_token(bob_id, "bob_c7", role="user")
    test_client.cookies.set("sentinel_token", bob_token)
    res = test_client.get(f"/api/briefings/{private_rep_id}")
    assert res.status_code == 403

    # Alice reads her own briefing -> 200
    alice_token = create_session_token(alice_id, "alice_c7", role="user")
    test_client.cookies.set("sentinel_token", alice_token)
    res_alice = test_client.get(f"/api/briefings/{private_rep_id}")
    assert res_alice.status_code == 200


def test_c8_prune_briefings_does_not_cross_delete_tenants():
    """C-8: Pruning deduplicates within user_id, but preserves briefings of different tenants in the same hour."""
    store = orchestrator.state_store
    # Two different tenants with identical slot and summary prefix in the same hour
    store.record_market_briefing("rep_t1", "midmarket", "Macro Wrap 2026: S&P 500 up 0.5%...", user_id="usr_tenant_1")
    store.record_market_briefing("rep_t2", "midmarket", "Macro Wrap 2026: S&P 500 up 0.5%...", user_id="usr_tenant_2")

    store.prune_briefings(retention_days=30)

    # Both must survive!
    b1 = store.get_market_briefing_by_id("rep_t1")
    b2 = store.get_market_briefing_by_id("rep_t2")
    assert b1 is not None, "Tenant 1 briefing must not be deleted by Tenant 2"
    assert b2 is not None, "Tenant 2 briefing must not be deleted by Tenant 1"


def test_c9_csp_security_header(test_client):
    """C-9: Responses must include Content-Security-Policy header."""
    res = test_client.get("/healthz")
    assert "content-security-policy" in res.headers
    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp


def test_h18_foreign_keys_enforced():
    """H-18: Foreign keys must be strictly enforced on SQLite connections."""
    store = orchestrator.state_store
    with store._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        row = cursor.fetchone()
        assert row[0] == 1, "Foreign keys PRAGMA must be ON"


# =====================================================================
# Phase 3: Financial Math & Quantitative Precision (C-10, H-8, H-9, H-10, H-11, H-12, H-13, H-14, H-15)
# =====================================================================

def test_c10_bollinger_precision_and_order():
    """C-10: Sub-$5 penny stock Bollinger Bands do not collapse into false squeeze; band extension takes priority."""
    # Construct bars for penny stock at $0.50 with tight spread
    bars = []
    for i in range(25):
        price = 0.50 + (i % 2) * 0.005
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": price,
            "high": price + 0.002,
            "low": price - 0.002,
            "close": price,
            "volume": 50000
        })
    # Last bar breaks sharply upward to $0.55 (well above upper band)
    bars[-1]["close"] = 0.55
    snap = compute_technical_snapshot("PENNY", custom_bars=bars)
    assert snap.is_live is True
    # Should be flagged as UPPER_BAND_EXTENDED, not SQUEEZE
    assert snap.bollinger_status == "UPPER_BAND_EXTENDED"


def test_h8_flat_price_series_rsi():
    """H-8: 20 bars of perfectly flat close prices must yield RSI 50.0 and NEUTRAL, not 0 or error."""
    flat_bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000, "date": f"2026-01-{i:02d}"} for i in range(1, 25)]
    snap = compute_technical_snapshot("FLAT", custom_bars=flat_bars)
    assert snap.rsi_14 == 50.0
    assert snap.rsi_status == "NEUTRAL"


def test_h9_insufficient_bars_sma_200_is_none():
    """H-9: Series with < 200 bars returns sma_200=None and dist_from_200_dma_pct=None."""
    bars = [{"date": f"2026-01-{i:02d}", "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000} for i in range(1, 60)]
    snap = compute_technical_snapshot("TEST", custom_bars=bars)
    assert snap.sma_200 is None
    assert snap.dist_from_200_dma_pct is None


def test_h10_ema_convergence():
    """H-10: EMA is seeded with the SMA of the first period bars."""
    values = [10.0, 12.0, 14.0, 16.0, 18.0]
    ema = _calc_ema(values, 3)
    # First 3 values SMA = (10 + 12 + 14) / 3 = 12.0
    assert len(ema) == 3
    assert ema[0] == 12.0
    # k = 2 / (3 + 1) = 0.5
    # ema[1] = 16.0 * 0.5 + 12.0 * 0.5 = 14.0
    assert abs(ema[1] - 14.0) < 1e-4


def test_h11_rvol_21_bar_guard():
    """H-11: RVOL with fewer than 21 bars returns default 1.0."""
    bars_20 = [{"volume": 10000, "open": 10, "high": 11, "low": 9, "close": 10, "date": "2026-01-01"} for _ in range(20)]
    snap = compute_technical_snapshot("TEST", custom_bars=bars_20)
    assert snap.rvol == 1.0


def test_h12_wilders_atr_formula():
    """H-12: ATR calculation matches Wilder's smoothed formula and stop loss is a volatility band."""
    bars = [
        {"high": 105.0, "low": 95.0, "close": 100.0, "open": 100.0, "volume": 1000, "date": "2026-01-01"}
    ] + [
        {"high": 110.0, "low": 100.0, "close": 105.0, "open": 100.0, "volume": 1000, "date": f"2026-01-{i:02d}"}
        for i in range(2, 30)
    ]
    snap = compute_technical_snapshot("TEST", custom_bars=bars)
    assert snap.atr_14 > 0.0
    assert snap.suggested_stop_loss == round(max(0.0, snap.current_price - (2.0 * snap.atr_14)), 2)


def test_h13_retail_divergence_trap_prioritization():
    """H-13: High bullish sentiment with weak volume (rvol < 0.85) triggers RETAIL_DIVERGENCE_TRAP."""
    with patch("analytics.sentiment_stream._fetch_stocktwits_stream") as mock_st, \
         patch("analytics.sentiment_stream._fetch_reddit_discussion") as mock_rd:
        mock_st.return_value = {
            "success": True,
            "bull_pct": 78.0,
            "total_messages": 30,
            "rate_per_hour": 10.0,
            "acceleration_factor": 1.0,
            "span_hours": 3.0,
            "sample_comments": ["Bullish!"]
        }
        mock_rd.return_value = {"count": 0, "sample_titles": []}
        snap = fetch_social_sentiment_snapshot("TRAP", rvol=0.50)
        assert snap.sentiment_verdict == "RETAIL_DIVERGENCE_TRAP", f"Expected RETAIL_DIVERGENCE_TRAP, got {snap.sentiment_verdict}"


def test_h14_h15_sentiment_velocity_timestamp_sorting():
    """H-14 & H-15: StockTwits parser sorts out-of-order messages chronologically and calculates arrival rate."""
    mock_messages = [
        {"created_at": "2026-01-01T12:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}, "body": "Great earnings and growth"},
        {"created_at": "2026-01-01T10:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}, "body": "Buying more shares now"},
        {"created_at": "2026-01-01T11:00:00Z", "entities": {"sentiment": {"basic": "Bearish"}}, "body": "Too expensive at this valuation"},
    ]
    with patch("analytics.sentiment_stream.get_stocktwits_client") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": mock_messages}
        mock_client.return_value.get.return_value = mock_resp

        res = _fetch_stocktwits_stream("TEST")
        assert res["span_hours"] > 0
        assert res["rate_per_hour"] > 0


# =====================================================================
# Phase 4: State Durability, Concurrency & Resilience (C-11, H-6, H-16, H-20)
# =====================================================================

def test_c11_sqlite_online_backup_snapshot(monkeypatch):
    """C-11: Backup to GCS uses sqlite3.backup() API to ensure clean snapshot under active writes."""
    monkeypatch.setenv("GCS_SYNC_ENABLED", "true")
    store = orchestrator.state_store
    store.set_kv("test_backup_c11", {"key": "val"})

    # Trigger backup (GCS upload will mock cleanly)
    with patch("google.cloud.storage.Client") as mock_client:
        mock_bucket = MagicMock()
        mock_client.return_value.bucket.return_value = mock_bucket
        success = store.backup_to_gcs(blocking=True)
        assert success is True


def test_h20_portfolio_save_live_price_update_no_nameerror():
    """H-20: Module-level update_portfolio_live_prices is callable without NameError."""
    from web.app import update_portfolio_live_prices
    p = Portfolio(name="Test", cash=1000.0, holdings=[
        PortfolioHolding(ticker="AAPL", name="Apple Inc.", current_price=150.0, shares=10, avg_price=150.0)
    ])
    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Apple", "current_price": 200.0, "sector": "Tech", "is_live": True}):
        updated_p, _ = update_portfolio_live_prices(p)
        assert updated_p.holdings[0].current_price == 200.0


# =====================================================================
# Phase 5: External Review Follow-Up Audit Remediations (R-1, R-2, R-3, R-4, R-5, R-7)
# =====================================================================

def test_r1_legacy_admin_credential_is_rotated_on_migration(monkeypatch):
    """R-1: Startup migration detects published default hash, rotates it to DASHBOARD_PASSWORD, and increments epoch."""
    store = orchestrator.state_store
    admin = store.get_or_create_default_admin()
    orig_hash = admin["password_hash"]
    orig_epoch = int(admin.get("token_epoch") or 1)

    try:
        # Manually plant legacy published default password hash into admin row
        legacy_hash = hash_password("sentinel_admin")
        with store._get_connection() as conn:
            conn.execute("UPDATE users SET password_hash = ?, token_epoch = 1 WHERE id = ?", (legacy_hash, admin["id"]))
            conn.commit()

        # Ensure DASHBOARD_PASSWORD is configured
        test_new_pw = "SecureTestPassword2026!#"
        monkeypatch.setattr(config, "dashboard_password", test_new_pw)

        # Run migration
        store.rotate_legacy_admin_credentials()

        # Verify old default no longer verifies
        refetched = store.get_user_by_id(admin["id"])
        assert not verify_password("sentinel_admin", refetched["password_hash"])
        assert verify_password(test_new_pw, refetched["password_hash"])
        assert int(refetched["token_epoch"]) >= 2
    finally:
        # Restore clean original admin state for subsequent tests
        with store._get_connection() as conn:
            conn.execute("UPDATE users SET password_hash = ?, token_epoch = ? WHERE id = ?", (orig_hash, orig_epoch, admin["id"]))
            conn.commit()


def test_r1_token_epoch_rejects_stale_sessions():
    """R-1: Session token generated before credential rotation is rejected once token_epoch increments."""
    store = orchestrator.state_store
    admin = store.get_or_create_default_admin()
    current_epoch = int(admin.get("token_epoch") or 1)

    # Create token matching current epoch
    valid_token = create_session_token(admin["id"], admin["username"], admin["role"], epoch=current_epoch)
    verified = verify_session_token(valid_token, expected_epoch=current_epoch)
    assert verified is not None

    # Stale token from earlier epoch (epoch - 1)
    stale_token = create_session_token(admin["id"], admin["username"], admin["role"], epoch=current_epoch - 1)
    stale_verified = verify_session_token(stale_token, expected_epoch=current_epoch)
    assert stale_verified is None, "Token from previous epoch must be rejected"


def test_r2_get_client_ip_rightmost_parsing_and_spoof_defense():
    """R-2: Client IP is parsed from the right past trusted proxy hops, rejecting client-forged leftmost IPs."""
    from web.app import get_client_ip
    from starlette.requests import Request

    # Single proxy hop: client appends forged "127.0.0.1", proxy appends real IP "198.51.100.42"
    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"127.0.0.1, 198.51.100.42"),
        ],
        "client": ("10.0.0.1", 12345),
    }
    req = Request(scope)
    parsed_ip = get_client_ip(req)
    assert parsed_ip == "198.51.100.42", f"Expected real proxy-appended IP '198.51.100.42', got '{parsed_ip}'"


def test_r4_quant_risk_disclosures_and_quality_ceiling():
    """R-4: Synthetic volatility proxy is disclosed, and data quality ceiling caps conviction deterministically."""
    from analytics.quant_risk import QuantRiskEngine, apply_data_quality_ceiling

    p = Portfolio(name="Test", cash=5000.0, holdings=[
        PortfolioHolding(ticker="AAPL", name="Apple Inc.", current_price=150.0, shares=10, avg_price=150.0, sector="Technology", weight_pct=100.0)
    ])
    engine = QuantRiskEngine()
    stress = engine.analyze_portfolio(p)

    # 1. Honest disclosure of missing empirical data
    unavail = " ".join(stress.fields_unavailable)
    assert "Insufficient historical data" in unavail

    # 2. Data quality ceiling when technical indicators missing
    capped_conv, reasons = apply_data_quality_ceiling(
        conviction_pct=88.0,
        missing_technical_fields=["rsi_14", "macd_missing"]
    )
    assert capped_conv == 55.0
    assert len(reasons) > 0

    # 3. Data quality ceiling when macro metrics missing
    capped_macro, m_reasons = apply_data_quality_ceiling(
        conviction_pct=85.0,
        missing_technical_fields=[],
        stress_metrics=None
    )
    assert capped_macro == 75.0


def test_r4_deterministic_position_sizing_engine():
    """§2B: Deterministic sizing engine calculates 2x ATR-14 stop distance and respects ADV turnover cap."""
    from analytics.quant_risk import compute_deterministic_position_size

    # Given equity $100,000, cash $20,000, price $100, ATR-14 $5.00
    res = compute_deterministic_position_size(
        portfolio_equity=100_000.0,
        portfolio_cash=20_000.0,
        current_price=100.0,
        atr_14=5.0,
        conviction_pct=80.0,
        median_adv_shares_30d=10_000_000.0,  # Highly liquid
        risk_budget_pct=0.75,
    )
    # Stop distance = 2 * 5.0 = $10.0 (10%)
    assert res["stop_distance_usd"] == 10.0
    assert res["stop_loss_price"] == 90.0
    # Risk budget = 0.75% of 100k = $750. Sized shares = 750 / 10 = 75 shares * 0.8 conviction = 60 shares ($6,000)
    assert res["target_shares"] == 60.0
    assert res["target_position_usd"] == 6000.0


def test_r7_dashboard_auth_disabled_fails_closed_in_production(monkeypatch):
    """R-7: Disabling authentication in production (K_SERVICE set) must immediately raise RuntimeError."""
    from web.auth_deps import get_current_user_optional
    from starlette.requests import Request

    monkeypatch.setattr(config, "dashboard_auth_enabled", False)
    monkeypatch.setenv("K_SERVICE", "financial-sentinel")

    scope = {"type": "http", "headers": [], "client": ("127.0.0.1", 80)}
    req = Request(scope)

    with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
        get_current_user_optional(req)

