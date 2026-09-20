"""
Unit tests for MarketBriefingAgent and DailyMarketScheduler.
"""
import pytest
from datetime import datetime
from freezegun import freeze_time
from zoneinfo import ZoneInfo
from models import Portfolio, PortfolioHolding, NewsItem, NewsCategory
from agents.market_briefing_agent import MarketBriefingAgent
from scheduler import DailyMarketScheduler
from analytics.market_data import fetch_market_overview, fetch_market_movers
from starlette.testclient import TestClient
from web.app import app


@pytest.fixture
def sample_portfolio():
    return Portfolio(
        name="Test Monitored Portfolio",
        cash=10000.0,
        holdings=[
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA Corporation",
                shares=50,
                avg_price=120.0,
                current_price=215.0,
                sector="Semiconductors"
            ),
            PortfolioHolding(
                ticker="AAPL",
                name="Apple Inc.",
                shares=40,
                avg_price=170.0,
                current_price=315.0,
                sector="Technology"
            )
        ]
    )


@pytest.fixture
def sample_news():
    return [
        NewsItem(
            id="news_1",
            title="Federal Reserve Signals Interest Rate Trajectory",
            source="wsj.com",
            url="https://wsj.com/fed",
            published_at=datetime.utcnow(),
            summary="Fed Chairman discusses inflation progress and balanced labor market risks.",
            category=NewsCategory.MACRO,
            related_tickers=[],
            related_sectors=["Financials"]
        ),
        NewsItem(
            id="news_2",
            title="Next-Gen Semiconductor Fabrication Expansions Announced",
            source="bloomberg.com",
            url="https://bloomberg.com/semi",
            published_at=datetime.utcnow(),
            summary="TSMC and partners expand multi-billion dollar advanced packaging lines.",
            category=NewsCategory.SECTOR,
            related_tickers=["NVDA", "TSM"],
            related_sectors=["Semiconductors"]
        )
    ]


def test_market_briefing_agent_premarket(sample_portfolio, sample_news, monkeypatch):
    agent = MarketBriefingAgent()
    monkeypatch.setattr(agent, "query_llm_text", lambda *args, **kwargs: "🌅 <b>PRE-MARKET INTELLIGENCE</b>\nMacro Tone: Bullish\nPortfolio Impact: Positive")
    overview = fetch_market_overview()
    msg = agent.generate_premarket_briefing(sample_portfolio, overview, sample_news, api_key="test_key")
    assert msg is not None
    assert "PRE-MARKET" in msg
    assert len(msg) > 50


def test_market_briefing_agent_midmarket(sample_portfolio, sample_news, monkeypatch):
    agent = MarketBriefingAgent()
    monkeypatch.setattr(agent, "query_llm_text", lambda *args, **kwargs: "☀️ <b>MID-MARKET PULSE</b>\nIntraday Action: Strong breadth\nPortfolio: Tracking solid")
    overview = fetch_market_overview()
    msg = agent.generate_midmarket_briefing(sample_portfolio, overview, sample_news, api_key="test_key")
    assert msg is not None
    assert "MID-MARKET" in msg
    assert len(msg) > 50


def test_market_briefing_agent_postmarket(sample_portfolio, sample_news, monkeypatch):
    agent = MarketBriefingAgent()
    monkeypatch.setattr(agent, "query_llm_text", lambda *args, **kwargs: "🌙 <b>POST-MARKET WRAP</b>\nClosing Summary: S&P up\nWinners: NVDA, CEG")
    overview = fetch_market_overview()
    movers = fetch_market_movers()
    msg = agent.generate_postmarket_briefing(sample_portfolio, overview, sample_news, movers, api_key="test_key")
    assert msg is not None
    assert "POST-MARKET" in msg
    assert len(msg) > 50


def test_market_briefing_agent_weekend(sample_portfolio, sample_news, monkeypatch):
    agent = MarketBriefingAgent()
    monkeypatch.setattr(agent, "query_llm_text", lambda *args, **kwargs: "🌟 <b>WEEKEND MACRO & WEEK-AHEAD PREVIEW</b>\nSunday Sentiment: Positive futures open")
    overview = fetch_market_overview()
    msg = agent.generate_weekend_eod_briefing(sample_portfolio, overview, sample_news, api_key="test_key")
    assert msg is not None
    assert "WEEKEND" in msg
    assert len(msg) > 50


def test_market_briefing_agent_earnings(sample_portfolio, sample_news, monkeypatch):
    agent = MarketBriefingAgent()
    # Test _format_holdings_summary directly
    holdings_text = agent._format_holdings_summary(sample_portfolio)
    assert "NVDA" in holdings_text
    assert "Sector:" in holdings_text

    # Mock query_llm_text and test generate_weekly_earnings_briefing
    monkeypatch.setattr(agent, "query_llm_text", lambda *args, **kwargs: "📅 <b>UPCOMING 7-DAY EARNINGS CALENDAR & SENTIMENT</b>\nVerified Releases: NVDA, ORCL")
    msg = agent.generate_weekly_earnings_briefing(sample_portfolio, sample_news, api_key="test_key")
    assert msg is not None
    assert "EARNINGS" in msg
    assert len(msg) > 30



def test_daily_market_scheduler_execution(sample_portfolio):
    scheduler = DailyMarketScheduler(
        portfolio_loader=lambda: sample_portfolio
    )
    # Test slot execution directly
    msg = scheduler.execute_briefing("premarket")
    assert msg is not None
    assert len(msg) > 50
    assert scheduler.tz == ZoneInfo("America/Los_Angeles")


def test_daily_market_scheduler_user_on_demand_persistence(sample_portfolio, monkeypatch):
    scheduler = DailyMarketScheduler(
        portfolio_loader=lambda: sample_portfolio
    )
    # Mock LLM generation
    mock_html = "☀️ <b>MID-MARKET PULSE & MOMENTUM (10:00 AM PST)</b>\n- Tech leading rotation."
    monkeypatch.setattr(scheduler.briefing_agent, "generate_midmarket_briefing", lambda *args, **kwargs: mock_html)

    # Execute on-demand for user_123 without auto dispatch to telegram
    msg = scheduler.execute_briefing("midmarket", user_id="user_123", auto_dispatch=False, force=True)
    assert msg == mock_html

    # Check that briefing was recorded in state_store
    briefings = scheduler.orchestrator.state_store.get_market_briefings(user_id="user_123", slot="midmarket")
    assert len(briefings) >= 1
    latest = briefings[0]
    assert latest["slot"] == "midmarket"
    assert "MID-MARKET" in latest["message_html"]


def test_telegram_briefing_auto_dispatch_and_archive(sample_portfolio, monkeypatch):
    scheduler = DailyMarketScheduler(
        portfolio_loader=lambda: sample_portfolio
    )
    mock_html = "🌅 <b>PRE-MARKET INTELLIGENCE (6:30 AM PST)</b>\n- S&P futures up +0.4%."
    monkeypatch.setattr(scheduler.briefing_agent, "generate_premarket_briefing", lambda *args, **kwargs: mock_html)

    sent_messages = []
    monkeypatch.setattr(scheduler.telegram, "send_message", lambda msg, chat_id: sent_messages.append((msg, chat_id)))

    # Execute on-demand briefing with auto_dispatch=True targeting chat_999
    msg = scheduler.execute_briefing("premarket", target_chat_id="chat_999", user_id="user_tg_1", auto_dispatch=True, force=True)
    assert msg == mock_html
    assert len(sent_messages) == 1
    assert sent_messages[0][1] == "chat_999"

    # Verify state_store archive has dispatched_channels=['telegram']
    briefings = scheduler.orchestrator.state_store.get_market_briefings(user_id="user_tg_1", slot="premarket")
    assert len(briefings) >= 1
    tg_briefing = briefings[0]
    assert tg_briefing["slot"] == "premarket"
    assert tg_briefing["dispatched_channels"] == ["telegram"]
    assert "PRE-MARKET" in tg_briefing["message_html"]


def test_briefing_deduplication_and_pruning(sample_portfolio):
    scheduler = DailyMarketScheduler(
        portfolio_loader=lambda: sample_portfolio
    )
    store = scheduler.orchestrator.state_store

    # Insert two identical briefings in same hour slot simulating previous double-write
    store.record_market_briefing(
        briefing_id="dup_test_1",
        slot="midmarket",
        message="☀️ <b>MID-MARKET DUPLICATE TEST</b>",
        user_id="usr_test"
    )
    store.record_market_briefing(
        briefing_id="dup_test_2",
        slot="midmarket",
        message="☀️ <b>MID-MARKET DUPLICATE TEST</b>",
        user_id="usr_test"
    )

    # get_market_briefings should return deduplicated list (only 1)
    briefings = store.get_market_briefings(user_id="usr_test", slot="midmarket")
    test_briefings = [b for b in briefings if "MID-MARKET DUPLICATE TEST" in b["message_html"]]
    assert len(test_briefings) == 1

    # Prune should delete duplicate rows from SQLite table
    pruned = store.prune_briefings(retention_days=30)
    assert pruned >= 1


@freeze_time("2026-09-07 13:30:00")  # Monday Labor Day 06:30 AM PDT
def test_holiday_intraday_briefings_suppressed(sample_portfolio, monkeypatch):
    """On an official NYSE holiday, intraday premarket is suppressed unless explicitly forced."""
    scheduler = DailyMarketScheduler(portfolio_loader=lambda: sample_portfolio)
    called = []
    monkeypatch.setattr(scheduler.briefing_agent, "generate_premarket_briefing", lambda *a, **kw: called.append("premarket") or "mock_premarket")

    # 1. Unforced premarket trigger on holiday should be skipped
    res = scheduler.execute_briefing("premarket", force=False)
    assert "Skipped premarket briefing on market holiday" in res
    assert "NYSE closed" in res
    assert len(called) == 0

    # 2. Forced premarket trigger should proceed
    res_forced = scheduler.execute_briefing("premarket", force=True)
    assert res_forced == "mock_premarket"
    assert len(called) == 1


@freeze_time("2026-09-08 04:00:00")  # Monday Labor Day 09:00 PM PDT
def test_holiday_evening_briefing_executed(sample_portfolio, monkeypatch):
    """On an official NYSE holiday, the evening 9:00 PM briefing executes with holiday context and next-session setup."""
    scheduler = DailyMarketScheduler(portfolio_loader=lambda: sample_portfolio)
    scheduler._executed_slots.clear()
    scheduler.orchestrator.state_store.set_kv("executed_schedule_slots", [])
    captured_prompts = []

    def mock_query(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return "🌟 <b>HOLIDAY MACRO & NEXT-SESSION PREVIEW (9:00 PM PST)</b>\n- Overnight futures firm."

    monkeypatch.setattr(scheduler.briefing_agent, "query_llm_text", mock_query)

    # Executing weekend slot on Monday Labor Day
    res = scheduler.execute_briefing("weekend", force=False)
    assert "HOLIDAY MACRO & NEXT-SESSION PREVIEW" in res
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "HOLIDAY MACRO & NEXT-SESSION" in prompt
    assert "Market Holiday (NYSE Closed)" in prompt
    assert "Tuesday, September 08, 2026" in prompt


@freeze_time("2026-09-07 13:30:00")  # Monday Labor Day 06:30 AM PDT / 09:30 AM EDT
def test_api_trigger_holiday_skip(monkeypatch):
    """The /api/schedule/trigger/{slot} endpoint skips unforced intraday slots on holidays."""
    from config import config

    monkeypatch.setattr(config, "dashboard_auth_enabled", True)
    monkeypatch.setattr(config, "cron_secret", "test_cron_secret")

    client = TestClient(app)
    # 1. Unforced premarket on Labor Day -> skipped
    resp = client.post(
        "/api/schedule/trigger/premarket",
        headers={"X-Cron-Secret": "test_cron_secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"
    assert "Market holiday today" in data["message"]

    # 2. Forced premarket on Labor Day -> success
    monkeypatch.setattr("web.app.briefing_service.generate_briefing", lambda *a, **kw: "Forced premarket text")
    resp_forced = client.post(
        "/api/schedule/trigger/premarket?force=true",
        headers={"X-Cron-Secret": "test_cron_secret"}
    )
    assert resp_forced.status_code == 200
    data_forced = resp_forced.json()
    assert data_forced["status"] == "success"




