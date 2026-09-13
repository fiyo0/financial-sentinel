"""
Unit and integration tests for FastAPI Web Dashboard endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from web.app import app


@pytest.fixture
def client():
    return TestClient(app)


from config import config

def test_login_and_auth_flow(client):
    # Unauthenticated access to / with auth enabled should redirect or show login
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (200, 302, 307)

    # Post invalid password via JSON API
    resp = client.post("/api/auth/login", json={"password": "wrongpassword"})
    assert resp.status_code == 401
    assert "Invalid" in resp.text

    # Post correct password via JSON API
    correct_pwd = config.dashboard_password or "sentinel_admin"
    resp = client.post("/api/auth/login", json={"password": correct_pwd})
    assert resp.status_code == 200
    assert "sentinel_token" in resp.cookies or "sentinel_auth" in resp.cookies



def test_dashboard_authenticated(client):
    correct_pwd = config.dashboard_password or "sentinel_admin"
    resp = client.get("/", headers={"X-Sentinel-Auth": correct_pwd}, cookies={"sentinel_auth": correct_pwd})
    assert resp.status_code == 200
    assert "Financial Sentinel" in resp.text
    assert "Portfolio & Risk" in resp.text
    assert "Earnings & Events" in resp.text


def test_api_earnings_calendar(client, monkeypatch):
    from unittest.mock import MagicMock
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "rows": [
                {
                    "symbol": "NVDA",
                    "name": "NVIDIA Corporation",
                    "time": "time-after-hours",
                    "marketCap": "$3,100,000,000,000",
                    "epsForecast": "$0.65",
                    "lastYearEPS": "$0.27"
                }
            ]
        }
    }
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: mock_resp)

    correct_pwd = config.dashboard_password or "sentinel_admin"
    resp = client.get("/api/earnings/calendar", headers={"X-Sentinel-Auth": correct_pwd}, cookies={"sentinel_auth": correct_pwd})
    assert resp.status_code == 200
    data = resp.json()
    assert "schedule" in data
    assert len(data["schedule"]) > 0


def test_api_portfolio_cash_update(client):
    correct_pwd = config.dashboard_password or "sentinel_admin"
    resp = client.post("/api/portfolio/cash", json={"cash": 12500.0}, headers={"X-Sentinel-Auth": correct_pwd}, cookies={"sentinel_auth": correct_pwd})
    assert resp.status_code == 200
    data = resp.json()
    assert data["portfolio"]["cash"] == 12500.0
    assert "stress" in data


def test_api_portfolio_save_and_add(client):
    correct_pwd = config.dashboard_password or "sentinel_admin"
    headers = {"X-Sentinel-Auth": correct_pwd}
    cookies = {"sentinel_auth": correct_pwd}

    # 1. Test /api/quote/{ticker}
    resp = client.get("/api/quote/NVDA", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    q = resp.json()
    assert q["ticker"] == "NVDA"

    # 2. Test /api/portfolio/save
    payload = {
        "name": "My Custom Portfolio",
        "cash": 25000.0,
        "holdings": [
            {"ticker": "NVDA", "name": "NVIDIA", "shares": 15, "avg_price": 110.0, "current_price": 125.0, "sector": "Semiconductors"},
            {"ticker": "AAPL", "name": "Apple", "shares": 30, "avg_price": 200.0, "current_price": 225.0, "sector": "Technology"}
        ]
    }
    resp = client.post("/api/portfolio/save", json=payload, headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["portfolio"]["cash"] == 25000.0
    assert len(data["portfolio"]["holdings"]) == 2
    tickers = [h["ticker"] for h in data["portfolio"]["holdings"]]
    assert "NVDA" in tickers
    assert "AAPL" in tickers

    # 3. Test /api/portfolio/add (alias)
    add_payload = {
        "ticker": "MSFT",
        "name": "Microsoft Corp",
        "shares": 10,
        "avg_price": 400.0,
        "current_price": 420.0,
        "sector": "Technology"
    }
    resp = client.post("/api/portfolio/add", json=add_payload, headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    tickers = [h["ticker"] for h in data["portfolio"]["holdings"]]
    assert "MSFT" in tickers

    # 4. Test /api/portfolio/holding/delete credits cash
    init_cash = data["portfolio"]["cash"]
    resp = client.post("/api/portfolio/holding/delete", json={"ticker": "MSFT"}, headers=headers, cookies=cookies)
    assert resp.status_code == 200
    del_data = resp.json()
    assert del_data["status"] == "success"
    # 10 shares * $420 current_price = $4,200 credited to cash
    assert del_data["portfolio"]["cash"] == init_cash + 4200.0
    tickers_after = [h["ticker"] for h in del_data["portfolio"]["holdings"]]
    assert "MSFT" not in tickers_after

    # 5. Test /api/portfolio returns briefing key for persistent recommendations
    resp = client.get("/api/portfolio", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    p_data = resp.json()
    assert "portfolio" in p_data
    assert "stress" in p_data
    assert "briefing" in p_data


def test_api_trigger_scan_structure(client, monkeypatch):
    from web.app import orchestrator
    from models import BriefingReport, PortfolioStressMetric
    from datetime import datetime

    correct_pwd = config.dashboard_password or "sentinel_admin"
    headers = {"X-Sentinel-Auth": correct_pwd}
    cookies = {"sentinel_auth": correct_pwd}

    # Ensure admin key is available for test
    monkeypatch.setattr(orchestrator, "resolve_user_api_key", lambda uid: "mock_test_key")

    mock_briefing = BriefingReport(
        report_id="rep_test_scan_123",
        generated_at=datetime.utcnow(),
        total_holdings_monitored=2,
        raw_news_count=5,
        portfolio_stress=PortfolioStressMetric(
            portfolio_var_95=2.1,
            portfolio_cvar_95=3.4,
            max_holding_weight=25.0,
            sector_herfindahl_index=0.22,
            top_sector="Technology",
            beta_to_sp500=1.1,
            estimated_max_drawdown=12.5,
            stress_loss_dollar=1500.0,
            stress_loss_percent=5.2
        ),
        critical_risk_alerts=[],
        medium_risk_alerts=[],
        top_opportunities=[],
        critic_verdicts=[],
        executive_summary="Test executive summary.",
        macro_tailwinds=["AI Compute"],
        macro_headwinds=["Inflation"],
        recommended_actions=[]
    )

    monkeypatch.setattr(orchestrator, "run_monitoring_cycle", lambda **kwargs: mock_briefing)

    resp = client.post("/api/scan?live=false&use_demo=false", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["report_id"] == "rep_test_scan_123"
    assert "briefing" in data
    assert "portfolio" in data
    assert "stress" in data


def test_api_trigger_scan_stream_sse(client, monkeypatch):
    import json
    from web.app import orchestrator
    from models import BriefingReport, PortfolioStressMetric
    from datetime import datetime

    correct_pwd = config.dashboard_password or "sentinel_admin"
    headers = {"X-Sentinel-Auth": correct_pwd}
    cookies = {"sentinel_auth": correct_pwd}

    monkeypatch.setattr(orchestrator, "resolve_user_api_key", lambda uid: "mock_test_key")

    mock_briefing = BriefingReport(
        report_id="rep_test_stream_456",
        generated_at=datetime.utcnow(),
        total_holdings_monitored=2,
        raw_news_count=5,
        portfolio_stress=PortfolioStressMetric(
            portfolio_var_95=2.1,
            portfolio_cvar_95=3.4,
            max_holding_weight=25.0,
            sector_herfindahl_index=0.22,
            top_sector="Technology",
            beta_to_sp500=1.1,
            estimated_max_drawdown=12.5,
            stress_loss_dollar=1500.0,
            stress_loss_percent=5.2
        ),
        critical_risk_alerts=[],
        medium_risk_alerts=[],
        top_opportunities=[],
        critic_verdicts=[],
        executive_summary="Test streaming summary.",
        macro_tailwinds=["AI Compute"],
        macro_headwinds=["Inflation"],
        recommended_actions=[]
    )

    def mock_run_monitoring_cycle(**kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress:
            on_progress("market_data", 10, "Fetching quotes...")
            on_progress("critic_audit", 75, "Critic auditing...")
        return mock_briefing

    monkeypatch.setattr(orchestrator, "run_monitoring_cycle", mock_run_monitoring_cycle)

    resp = client.get("/api/scan/stream?live=false&use_demo=false", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    lines = resp.text.strip().split("\n\n")
    events = []
    for line in lines:
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    assert len(events) >= 2
    types = [e.get("type") for e in events]
    assert "progress" in types
    assert "complete" in types

    complete_evt = next(e for e in events if e.get("type") == "complete")
    assert complete_evt["report_id"] == "rep_test_stream_456"
    assert "briefing" in complete_evt


def test_api_market_briefings_endpoints(client, monkeypatch):
    from web.app import daily_scheduler, orchestrator

    correct_pwd = config.dashboard_password or "sentinel_admin"
    headers = {"X-Sentinel-Auth": correct_pwd}
    cookies = {"sentinel_auth": correct_pwd}

    # 1. Test GET /api/briefings returns slots and list
    resp = client.get("/api/briefings", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "briefings" in data
    assert "slots" in data
    assert "premarket" in data["slots"]
    assert "midmarket" in data["slots"]
    assert "postmarket" in data["slots"]
    assert "weekend" in data["slots"]
    assert "earnings" in data["slots"]

    # 2. Test POST /api/briefings/generate with mock scheduler
    mock_msg = "🌅 <b>PRE-MARKET INTELLIGENCE</b>\n• Futures up 0.4%\n• NVDA catalyst active."

    def mock_execute_briefing(slot, chat_id, user_id, dispatch, force):
        orchestrator.state_store.record_market_briefing(
            briefing_id=f"test_{slot}_999",
            slot=slot,
            message=mock_msg,
            user_id=user_id,
            dispatched_channels=["telegram"] if dispatch else []
        )
        return mock_msg

    monkeypatch.setattr(daily_scheduler, "execute_briefing", mock_execute_briefing)

    gen_payload = {"slot": "premarket", "dispatch_telegram": False}
    gen_resp = client.post("/api/briefings/generate", json=gen_payload, headers=headers, cookies=cookies)
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["status"] == "success"
    assert gen_data["slot"] == "premarket"
    assert "PRE-MARKET" in gen_data["message_html"]
    assert gen_data["dispatched_telegram"] is False

    # 3. Verify the generated briefing is in the archive
    archive_resp = client.get("/api/briefings", headers=headers, cookies=cookies)
    assert archive_resp.status_code == 200
    archive_data = archive_resp.json()
    assert len(archive_data["briefings"]) >= 1
    found = next((b for b in archive_data["briefings"] if b["report_id"] == "test_premarket_999"), None)
    assert found is not None
    assert found["slot"] == "premarket"
    assert "PRE-MARKET" in found["message_html"]

    # 4. Test GET /api/briefings/{report_id}
    detail_resp = client.get("/api/briefings/test_premarket_999", headers=headers, cookies=cookies)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["status"] == "success"
    assert detail_data["briefing"]["report_id"] == "test_premarket_999"

    # 5. Test POST /api/briefings/generate with earnings slot
    mock_earnings_msg = "📅 <b>UPCOMING 7-DAY EARNINGS CALENDAR</b>\n• ORCL: AMC\n• ADBE: AMC"
    def mock_earnings_briefing(slot, chat_id, user_id, dispatch, force):
        orchestrator.state_store.record_market_briefing(
            briefing_id=f"test_{slot}_123",
            slot=slot,
            message=mock_earnings_msg,
            user_id=user_id,
            dispatched_channels=[]
        )
        return mock_earnings_msg

    monkeypatch.setattr(daily_scheduler, "execute_briefing", mock_earnings_briefing)
    earn_resp = client.post("/api/briefings/generate", json={"slot": "earnings"}, headers=headers, cookies=cookies)
    assert earn_resp.status_code == 200
    earn_data = earn_resp.json()
    assert earn_data["slot"] == "earnings"
    assert "EARNINGS" in earn_data["message_html"]

    # Verify generated_at timestamp format has Z
    earn_detail = client.get("/api/briefings/test_earnings_123", headers=headers, cookies=cookies)
    assert earn_detail.status_code == 200
    b_obj = earn_detail.json()["briefing"]
    assert b_obj["slot"] == "earnings"
    assert b_obj["generated_at"].endswith("Z")

    # 6. Test invalid slot validation
    err_resp = client.post("/api/briefings/generate", json={"slot": "invalid_slot"}, headers=headers, cookies=cookies)
    assert err_resp.status_code == 400

    # 7. Test not found
    notfound_resp = client.get("/api/briefings/non_existent_briefing_id", headers=headers, cookies=cookies)
    assert notfound_resp.status_code == 404


def test_api_analyze_ticker(client, monkeypatch):
    from analytics.technical_indicators import TechnicalSnapshot
    from analytics.sentiment_stream import SentimentSnapshot
    from web.app import orchestrator

    fake_tech = TechnicalSnapshot(
        ticker="AAPL",
        current_price=220.0,
        rsi_14=62.5,
        rsi_status="NEUTRAL_BULLISH",
        macd_line=1.5,
        macd_signal=1.1,
        macd_hist=0.4,
        macd_status="BULLISH_MOMENTUM",
        bollinger_upper=230.0,
        bollinger_middle=215.0,
        bollinger_lower=200.0,
        bollinger_pct_b=0.67,
        bollinger_bandwidth=0.14,
        bollinger_status="NORMAL",
        sma_20=215.0,
        sma_50=210.0,
        sma_200=190.0,
        dist_from_50_dma_pct=4.76,
        dist_from_200_dma_pct=15.79,
        trend_alignment="STRONG_BULLISH",
        atr_14=3.5,
        suggested_stop_loss=213.0,
        is_live=True
    )

    fake_sent = SentimentSnapshot(
        ticker="AAPL",
        retail_bull_pct=72.0,
        retail_bear_pct=28.0,
        total_messages_analyzed=25,
        social_velocity="ELEVATED",
        reddit_post_count=5,
        relative_volume=1.4,
        composite_sentiment_score=72.0,
        sentiment_verdict="BULLISH_ACCUMULATION",
        is_live=True
    )

    monkeypatch.setattr("analytics.market_data.fetch_live_quote", lambda t: {"price": 220.0, "change_pct": 1.2})
    monkeypatch.setattr("analytics.technical_indicators.compute_technical_snapshot", lambda t: fake_tech)
    monkeypatch.setattr("analytics.sentiment_stream.fetch_social_sentiment_snapshot", lambda t, **kw: fake_sent)
    monkeypatch.setattr(orchestrator.analysis_agent, "analyze_single_ticker", lambda **kw: "Mocked Analysis for AAPL")

    correct_pwd = config.dashboard_password or "sentinel_admin"
    headers = {"X-Sentinel-Auth": correct_pwd}
    cookies = {"sentinel_auth": correct_pwd}

    resp = client.post("/api/analyze/AAPL", headers=headers, cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["ticker"] == "AAPL"
    assert data["technicals"]["rsi_14"] == 62.5
    assert data["sentiment"]["retail_bull_pct"] == 72.0
    assert data["analysis"] == "Mocked Analysis for AAPL"








