"""
Unit tests for temporal grounding and timestamped briefings in MarketBriefingAgent and StateStore.
"""
import pytest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from models import Portfolio, PortfolioHolding, NewsItem, NewsCategory
from agents.market_briefing_agent import MarketBriefingAgent
from storage.state_store import StateStore

PST = ZoneInfo("America/Los_Angeles")
EST = ZoneInfo("America/New_York")


@pytest.fixture
def sample_portfolio():
    return Portfolio(
        name="Test Portfolio",
        cash=50000.0,
        holdings=[
            PortfolioHolding(
                ticker="AMD",
                name="Advanced Micro Devices",
                shares=100,
                avg_price=150.0,
                current_price=160.0,
                sector="Semiconductors",
                daily_change_pct=2.1
            )
        ]
    )


def test_format_news_summary_relative_timestamps():
    """Verify news formatting includes chronological ordering and relative time tags."""
    agent = MarketBriefingAgent()
    as_of = datetime(2026, 9, 16, 15, 0, tzinfo=PST)  # 6:00 PM EDT

    # Create news items: 2 hours ago today, and 26 hours ago yesterday
    news_today = NewsItem(
        id="n_today",
        title="Fed Hikes Interest Rates by 25 bps",
        source="Federal Reserve Press",
        url="https://federalreserve.gov/press",
        published_at=datetime(2026, 9, 16, 14, 0, tzinfo=EST),  # 2:00 PM EDT today (4h ago)
        summary="FOMC voted to raise the target range for the federal funds rate.",
        category=NewsCategory.MACRO,
        related_tickers=[],
        related_sectors=["Financials"]
    )
    news_yesterday = NewsItem(
        id="n_yesterday",
        title="Coinbase Slips Ahead of Clarity Act",
        source="Yahoo Finance",
        url="https://finance.yahoo.com/coin",
        published_at=datetime(2026, 9, 15, 13, 0, tzinfo=EST),  # Yesterday 1:00 PM EDT (29h ago)
        summary="Crypto equities decline ahead of committee vote.",
        category=NewsCategory.MACRO,
        related_tickers=["COIN"],
        related_sectors=["Financials"]
    )

    formatted = agent._format_news_summary([news_yesterday, news_today], as_of=as_of)

    # 1. Newest article must appear first despite being passed second
    lines = formatted.split("\n")
    assert "Fed Hikes Interest Rates" in lines[0]
    assert "Coinbase Slips" in lines[1]

    # 2. Must contain relative age tags
    assert "Today 02:00 PM EDT" in lines[0]
    assert "4.0h ago" in lines[0]
    assert "Yesterday Sep 15" in lines[1]


def test_postmarket_prompt_temporal_grounding_september_16(sample_portfolio):
    """
    Simulates the September 16, 2026 3:00 PM PST Post-Market run.
    Ensures prompt strictly conveys that the FOMC rate decision is COMPLETED today,
    not happening tomorrow.
    """
    agent = MarketBriefingAgent()
    as_of_pst = datetime(2026, 9, 16, 15, 0, tzinfo=PST)

    captured_prompts = []
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), "🌙 <b>POST-MARKET WRAP</b>")[1]

    overview = {"indices": {"SPY": {"current_price": 550.0, "change_pct": -0.4}}}
    movers = {"top_gainers": [{"ticker": "AMD", "change_pct": 2.1}], "top_losers": []}
    news = [
        NewsItem(
            id="n1",
            title="Fed decision completed",
            source="WSJ",
            url="https://wsj.com",
            published_at=datetime(2026, 9, 16, 14, 5, tzinfo=EST),
            summary="Fed policy statement released.",
            category=NewsCategory.MACRO
        )
    ]

    msg = agent.generate_postmarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=news,
        market_movers=movers,
        api_key="test_key",
        as_of=as_of_pst
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]

    # Temporal ground truth assertions
    assert "Wednesday, September 16, 2026" in prompt
    assert ("03:00 PM PDT / 06:00 PM EDT" in prompt or "03:00 PM PST / 06:00 PM EDT" in prompt)
    assert "Post-Market Closing Wrap" in prompt


    # Economic calendar ground truth assertions
    assert "[COMPLETED] Federal Reserve FOMC Interest Rate Decision" in prompt
    assert "Concluded earlier today" in prompt
    assert "DO NOT describe it as upcoming, happening tomorrow, or in the future" in prompt

    # Tomorrow's focus ground truth assertions
    assert "tomorrow's session (2026-09-17)" in prompt
    assert "DO NOT describe events that occurred earlier today (such as completed Federal Reserve rate announcements" in prompt


def test_premarket_prompt_temporal_grounding_september_16(sample_portfolio):
    """
    Simulates the September 16, 2026 6:30 AM PST Pre-Market run.
    Ensures prompt conveys that the FOMC rate decision is upcoming later today (PENDING).
    """
    agent = MarketBriefingAgent()
    as_of_pst = datetime(2026, 9, 16, 6, 30, tzinfo=PST)

    captured_prompts = []
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), "🌅 <b>PRE-MARKET BRIEF</b>")[1]

    overview = {"indices": {}}
    agent.generate_premarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=[],
        api_key="test_key",
        as_of=as_of_pst
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]

    assert "Wednesday, September 16, 2026" in prompt
    assert ("06:30 AM PDT / 09:30 AM EDT" in prompt or "06:30 AM PST / 09:30 AM EDT" in prompt)
    assert "[PENDING] Federal Reserve FOMC Interest Rate Decision" in prompt

    assert "Upcoming today during the trading session" in prompt


def test_state_store_get_recent_news(tmp_path):
    """Verify get_recent_news retrieves news within the lookback window ordered by published_at DESC."""
    db_file = str(tmp_path / "test_state.db")
    store = StateStore(db_path=db_file)

    now = datetime.now(timezone.utc)
    item_new = NewsItem(
        id="item_new",
        title="Breaking: Tech Rally Continues",
        source="Bloomberg",
        url="https://bloomberg.com/tech",
        published_at=now - timedelta(hours=1),
        summary="Tech stocks advance.",
        category=NewsCategory.BREAKING
    )
    item_old = NewsItem(
        id="item_old",
        title="Old News from 40 hours ago",
        source="Reuters",
        url="https://reuters.com/old",
        published_at=now - timedelta(hours=40),
        summary="Historical summary.",
        category=NewsCategory.MACRO
    )

    store.save_news_item(item_new)
    store.save_news_item(item_old)

    # 24-hour lookback should retrieve only item_new
    recent = store.get_recent_news(hours=24)
    assert len(recent) == 1
    assert recent[0].id == "item_new"
    assert recent[0].title == "Breaking: Tech Rally Continues"

    # 48-hour lookback should retrieve both, with item_new first
    all_news = store.get_recent_news(hours=48)
    assert len(all_news) == 2
    assert all_news[0].id == "item_new"
    assert all_news[1].id == "item_old"


def test_market_briefings_no_tier1_boilerplate(sample_portfolio, monkeypatch):
    """
    Verify all market briefings (Pre, Mid, Post, Weekend) do NOT contain 'tier-1' puffery,
    that intraday briefs omit the 3-month forward schedule, and that Rule 4 is present.
    """
    monkeypatch.setattr("analytics.economic_calendar.fetch_live_fed_bulletins", lambda *args, **kwargs: [])
    agent = MarketBriefingAgent()
    # A calm day without FOMC decisions or scheduled releases
    as_of_pst = datetime(2026, 9, 21, 10, 0, tzinfo=PST)

    captured_prompts = []
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt + (kwargs.get("system_instruction") or "")), "☀️ <b>MID-MARKET PULSE</b>")[1]

    overview = {"indices": {"SPY": {"current_price": 550.0, "change_pct": 0.2}}}

    # Mid-Market Pulse
    agent.generate_midmarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=[],
        api_key="test_key",
        as_of=as_of_pst
    )
    assert len(captured_prompts) == 1
    mid_prompt = captured_prompts[0]

    # Verify no "tier-1" jargon appears
    assert "tier-1" not in mid_prompt.lower()
    # Verify Rule 4 exists
    assert "Selective & Relevant Macro / Fed Coverage" in mid_prompt
    assert "No releases or central bank statements scheduled for today or tomorrow" in mid_prompt
    # Verify intraday prompt omits the 3-month prospective horizon
    assert "PROSPECTIVE 3-MONTH CENTRAL BANK SCHEDULE" not in mid_prompt

    # Weekend Briefing
    captured_prompts.clear()
    weekend_dt = datetime(2026, 9, 20, 21, 0, tzinfo=PST)
    agent.generate_weekend_eod_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=[],
        api_key="test_key",
        as_of=weekend_dt
    )
    assert len(captured_prompts) == 1
    weekend_prompt = captured_prompts[0]

    # Weekend prompt should have the 3-month schedule without 'tier-1'
    assert "tier-1" not in weekend_prompt.lower()
    assert "PROSPECTIVE 3-MONTH CENTRAL BANK SCHEDULE" in weekend_prompt
    assert "Selective & Relevant Macro / Fed Coverage" in weekend_prompt


def test_portfolio_holding_catalyst_extraction_without_price_gate():
    """Verify holding product launches or filings are surfaced even if price movement is 0.0%."""
    portfolio = Portfolio(
        name="Tech Focus",
        cash=10000.0,
        holdings=[
            PortfolioHolding(
                ticker="AAPL",
                name="Apple Inc.",
                shares=50,
                avg_price=220.0,
                current_price=225.0,
                daily_change_pct=0.0,  # Zero price movement
                sector="Technology"
            )
        ]
    )
    news = [
        NewsItem(
            id="n_aapl",
            title="Apple Unveils New M4 Ultra Chip and AI Server Architecture",
            source="Bloomberg",
            url="https://bloomberg.com/aapl",
            published_at=datetime.now(timezone.utc),
            summary="New M4 chip targets enterprise AI workloads.",
            category=NewsCategory.BREAKING,
            related_tickers=["AAPL"]
        )
    ]
    agent = MarketBriefingAgent()
    movers_ctx = agent._extract_significant_portfolio_movers(portfolio, news, threshold_pct=1.5)

    # AAPL must be extracted despite 0.0% price move
    assert "AAPL" in movers_ctx
    assert "Apple Inc." in movers_ctx
    assert "Apple Unveils New M4 Ultra Chip" in movers_ctx
    assert "Core holdings calm" not in movers_ctx


def test_cross_briefing_context_propagation(sample_portfolio):
    """Verify morning gameplan summary is passed into midday prompt under cross-briefing continuity."""
    agent = MarketBriefingAgent()
    as_of_pst = datetime(2026, 9, 23, 10, 0, tzinfo=PST)

    captured_prompts = []
    mock_resp = "☀️ <b>MID-MARKET PULSE & MOMENTUM (10:00 AM PST)</b>\nIntraday Action: Broad indices advance on strong volume breadth."
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), mock_resp)[1]

    overview = {"indices": {"SPY": {"current_price": 550.0, "change_pct": 0.3}}}
    prior_context = "Morning Gameplan: Monitor SPY 550 pivot ahead of 1:00 PM ET Treasury auction. Take partial profits if yields spike."

    msg = agent.generate_midmarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=[],
        api_key="test_key",
        as_of=as_of_pst,
        prior_briefing_context=prior_context
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "CROSS-BRIEFING NARRATIVE CONTINUITY" in prompt
    assert "Monitor SPY 550 pivot ahead of 1:00 PM ET Treasury auction" in prompt
    assert "MID-MARKET PULSE" in msg


def test_midmarket_catalyst_followup(sample_portfolio):
    """Verify midday briefing includes morning catalyst digestion audit and afternoon roadmap."""
    agent = MarketBriefingAgent()
    as_of_pst = datetime(2026, 9, 23, 10, 0, tzinfo=PST)

    captured_prompts = []
    mock_resp = "☀️ <b>MID-MARKET PULSE & MOMENTUM (10:00 AM PST)</b>\nIntraday Action: Broad indices advance on strong volume breadth."
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), mock_resp)[1]

    overview = {"indices": {"SPY": {"current_price": 550.0, "change_pct": 0.3}}}
    news = [
        NewsItem(
            id="n_mid",
            title="Treasury 10-Year Auction Demand Hits Record",
            source="WSJ",
            url="https://wsj.com/auction",
            published_at=datetime.now(timezone.utc),
            summary="Yields drop 4 bps following strong direct bidder participation.",
            category=NewsCategory.MACRO
        )
    ]

    agent.generate_midmarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=news,
        api_key="test_key",
        as_of=as_of_pst
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "Morning Catalyst Digestion:" in prompt
    assert "Audit how the market digested morning economic prints" in prompt
    assert "Afternoon Posture:" in prompt or "Afternoon Roadmap" in prompt


def test_postmarket_amc_earnings_integration(sample_portfolio):
    """Verify verified AMC earnings from earnings calendar are incorporated into post-market prompts."""
    agent = MarketBriefingAgent()
    as_of_pst = datetime(2026, 9, 23, 15, 0, tzinfo=PST)

    captured_prompts = []
    mock_resp = "🌙 <b>POST-MARKET WRAP & DAY-END RECAP (3:00 PM PST)</b>\nClosing Bell: S&P 500 finishes in green."
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), mock_resp)[1]

    overview = {"indices": {"SPY": {"current_price": 550.0, "change_pct": 0.5}}}
    amc_earnings = [
        {
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "timing": "AMC (Post-Market)",
            "market_cap": 3000000000000,
            "market_cap_str": "$3.0T",
            "eps_forecast": "$0.75",
            "last_year_eps": "$0.40"
        }
    ]

    agent.generate_postmarket_briefing(
        portfolio=sample_portfolio,
        market_overview=overview,
        news_items=[],
        market_movers={},
        api_key="test_key",
        as_of=as_of_pst,
        earnings_calendar=amc_earnings
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "AFTER-MARKET-CLOSE (AMC) EARNINGS RELEASES" in prompt
    assert "NVDA" in prompt
    assert "NVIDIA Corporation" in prompt
    assert "AMC (Post-Market)" in prompt
    assert "Est. EPS: $0.75" in prompt


def test_state_store_get_recent_news_for_portfolio(tmp_path):
    """Verify get_recent_news_for_portfolio finds news by ticker, alias, and name."""
    db_file = str(tmp_path / "portfolio_news_test.db")
    store = StateStore(db_path=db_file)
    now = datetime.now(timezone.utc)

    item_ticker = NewsItem(
        id="item_nvda",
        title="NVIDIA Announces Quantum Computing Breakthrough",
        source="TechCrunch",
        url="https://tc.com/nvda",
        published_at=now - timedelta(hours=2),
        summary="NVDA shares react.",
        category=NewsCategory.BREAKING,
        related_tickers=["NVDA"]
    )
    item_alias = NewsItem(
        id="item_apple",
        title="Apple Expands Enterprise Data Centers in Texas",
        source="Reuters",
        url="https://reuters.com/aapl",
        published_at=now - timedelta(hours=3),
        summary="Expansion brings 2000 jobs.",
        category=NewsCategory.BREAKING,
        related_tickers=[]
    )
    item_other = NewsItem(
        id="item_other",
        title="Boeing Inspects 737 Fleet Maintenance",
        source="WSJ",
        url="https://wsj.com/ba",
        published_at=now - timedelta(hours=4),
        summary="Airlines inspect fleet.",
        category=NewsCategory.MACRO,
        related_tickers=["BA"]
    )

    store.save_news_item(item_ticker)
    store.save_news_item(item_alias)
    store.save_news_item(item_other)

    portfolio = Portfolio(
        name="My Portfolio",
        cash=50000.0,
        holdings=[
            PortfolioHolding(
                ticker="AAPL",
                name="Apple Inc.",
                shares=10,
                avg_price=200.0,
                current_price=220.0,
                sector="Technology"
            ),
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA Corp",
                shares=15,
                avg_price=100.0,
                current_price=120.0,
                sector="Semiconductors"
            )
        ]
    )

    holding_news = store.get_recent_news_for_portfolio(portfolio, hours=24)
    found_ids = [n.id for n in holding_news]
    assert "item_nvda" in found_ids
    assert "item_apple" in found_ids
    assert "item_other" not in found_ids
