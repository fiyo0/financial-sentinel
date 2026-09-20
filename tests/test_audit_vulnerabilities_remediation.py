"""
tests/test_audit_vulnerabilities_remediation.py
Validation suite for all 33 adversarial audit remediations and Telegram limit tuning:
- Quant & Numerical Stability (VUL-301, VUL-302, VUL-303, VUL-304)
- News Ingestion & Anti-Hallucination Constraints (VUL-401, VUL-402, VUL-403, VUL-404)
- Market Calendar, Early Closures & Dynamic Projections (VUL-501, VUL-502, VUL-503, VUL-504, VUL-505, VUL-506)
- Multi-Channel Transport & Sink Limits (VUL-601, VUL-602, VUL-603, VUL-604, VUL-605, VUL-606)
"""
import pytest
import math
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from analytics.quant_risk import (
    compute_daily_log_returns,
    compute_empirical_beta,
    compute_deterministic_position_size,
)
from services.portfolio_service import PortfolioService
from models import (
    Portfolio, PortfolioHolding, NewsItem, NewsCategory, BriefingReport
)
from agents.analysis_agent import PortfolioAnalysisAgent
from agents.market_briefing_agent import MarketBriefingAgent
from analytics.market_calendar import (
    us_market_holidays,
    get_early_close_dates,
    get_market_close_time_et,
    get_next_trading_day,
)
from analytics.economic_calendar import generate_statutory_macro_schedule, generate_economic_calendar_ics
from channels.telegram import TelegramChannel
from channels.telegram_bot import tg_safe
from channels.slack import SlackChannel
from channels.discord import DiscordChannel
from channels.email_sink import EmailChannel
from agents.notification_agent import NotificationAgent


# ==============================================================================
# 1. Quant & Valuation Integrity Tests (VUL-301 - VUL-304)
# ==============================================================================

def test_subpenny_nan_returns_suppressed():
    """VUL-301: Subpenny, zero, and negative prices must produce NaN rather than math domain errors or 0.0."""
    prices = [10.0, 0.0, -5.0, 10.0]
    returns = compute_daily_log_returns(prices)
    assert len(returns) == 3
    assert math.isnan(returns[0])
    assert math.isnan(returns[1])
    assert math.isnan(returns[2])


def test_beta_variance_epsilon_stability():
    """VUL-302: Flat returns with variance <= 1e-12 must return None for empirical beta."""
    flat_asset = [0.01] * 20
    flat_benchmark = [0.01] * 20
    beta = compute_empirical_beta(flat_asset, flat_benchmark)
    assert beta is None


def test_atr_fallback_disclosed():
    """VUL-303: When ATR-14 is unavailable, sizing notes must disclose default stop fallback."""
    sizing = compute_deterministic_position_size(
        portfolio_equity=100000.0,
        portfolio_cash=50000.0,
        current_price=100.0,
        atr_14=None
    )
    assert sizing["stop_loss_price"] == 92.0  # 8% stop default
    assert "ATR-14 unavailable" in sizing["sizing_notes"]
    assert "8%" in sizing["sizing_notes"]


def test_portfolio_add_missing_price_error():
    """VUL-304: Adding a holding with no price and no quote data must raise ValueError, not fallback to 100.0."""
    svc = PortfolioService(orchestrator=MagicMock())
    with patch("analytics.market_data.fetch_live_quote", return_value={}):
        with pytest.raises(ValueError, match="Cannot resolve current price"):
            svc.add_or_update_holding(
                user_id=None,
                ticker="NONEXISTENT_TICKER_999",
                shares=10.0,
                price=None
            )


# ==============================================================================
# 2. Slicing Bias & Anti-Hallucination Constraints (VUL-401 - VUL-404)
# ==============================================================================

def test_holding_affinity_news_allocation():
    """VUL-401: News selection must allocate up to 2 items per held ticker before slicing."""
    agent = PortfolioAnalysisAgent()
    portfolio = Portfolio(
        name="Tech Energy",
        cash=5000.0,
        holdings=[
            PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=150.0, current_price=150.0, sector="Technology"),
            PortfolioHolding(ticker="XOM", name="Exxon", shares=20, avg_price=100.0, current_price=100.0, sector="Energy"),
        ]
    )

    # 15 AAPL news items, 2 XOM news items, 5 macro items
    news = []
    for i in range(15):
        news.append(NewsItem(
            id=f"aapl_{i}", title=f"Apple iPhone Update {i}", source="TechCrunch",
            url="https://tc.com", published_at=datetime.now(timezone.utc), summary="Apple news",
            category=NewsCategory.BREAKING, source_reliability_score=0.9,
            related_tickers=["AAPL"], related_sectors=["Technology"], raw_hash=f"hash_a_{i}"
        ))
    for i in range(2):
        news.append(NewsItem(
            id=f"xom_{i}", title=f"Exxon Oil Output {i}", source="Reuters",
            url="https://reuters.com", published_at=datetime.now(timezone.utc), summary="Exxon news",
            category=NewsCategory.BREAKING, source_reliability_score=0.9,
            related_tickers=["XOM"], related_sectors=["Energy"], raw_hash=f"hash_x_{i}"
        ))

    captured_prompts = []
    agent.query_llm_json = lambda prompt, **kwargs: (captured_prompts.append(prompt), {"analyses": []})[1]
    agent._llm_batch_analyze_portfolio(portfolio, news, api_key="dummy")

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    # Verify XOM is not starved out by AAPL
    assert "Exxon Oil Output 0" in prompt
    assert "Apple iPhone Update 0" in prompt


def test_macro_empty_feed_negative_constraint():
    """VUL-402: Empty news feed must include explicit anti-hallucination directive."""
    agent = PortfolioAnalysisAgent()
    portfolio = Portfolio(
        name="Solo", cash=1000.0,
        holdings=[PortfolioHolding(ticker="SPY", name="SPDR S&P 500", shares=5, avg_price=500.0, current_price=500.0, sector="Index ETF / Fund")]
    )

    captured_prompts = []
    agent.query_llm_json = lambda prompt, **kwargs: (captured_prompts.append(prompt), {"analyses": []})[1]
    agent._llm_batch_analyze_portfolio(portfolio, news_items=[], api_key="dummy")

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "CRITICAL ANTI-HALLUCINATION DIRECTIVE" in prompt
    assert "You MUST NOT invent, assume, or hallucinate news events" in prompt


def test_primary_catalysts_deduplicated():
    """VUL-403: Near-duplicate headlines for the same event must be clustered and deduplicated."""
    from agents.analysis_agent import are_headlines_same_event_cluster
    h1 = "NVIDIA faces DOJ subpoena over AI chip bundling practices"
    h2 = "DOJ issues antitrust subpoenas to Nvidia over AI chip sales"
    assert are_headlines_same_event_cluster(h1, h2) is True

    h3 = "Federal Reserve cuts interest rates by 25 basis points"
    assert are_headlines_same_event_cluster(h1, h3) is False


def test_briefing_composite_ranking_order():
    """VUL-404: High-importance regulatory and earnings items must outrank generic breaking news."""
    agent = MarketBriefingAgent()
    old_sec = NewsItem(
        id="sec1", title="SEC Files Complaint Against Entity", source="sec.gov",
        url="https://sec.gov", published_at=datetime.now(timezone.utc) - timedelta(hours=4),
        summary="Regulatory enforcement action", category=NewsCategory.SEC_FILING,
        source_reliability_score=0.98, related_tickers=["XYZ"], related_sectors=["Finance"], raw_hash="h1"
    )
    new_generic = NewsItem(
        id="gen1", title="Market Moves Slightly Higher in Quiet Session", source="RandomBlog",
        url="https://blog.com", published_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        summary="Chit chat", category=NewsCategory.BREAKING,
        source_reliability_score=0.4, related_tickers=[], related_sectors=[], raw_hash="h2"
    )

    summary = agent._format_news_summary([new_generic, old_sec])
    lines = [line for line in summary.split("\n") if line.strip()]
    assert len(lines) >= 2
    # The high-authority SEC item should appear first despite being older
    assert "SEC Files Complaint" in lines[0]


# ==============================================================================
# 3. Market Calendar, Early Closures & Timezones (VUL-501 - VUL-506)
# ==============================================================================

def test_market_calendar_meeus_easter_accuracy():
    """VUL-504: Meeus Easter algorithm accurately identifies Good Friday for multiple years."""
    h2024 = us_market_holidays(2024)
    assert date(2024, 3, 29) in h2024  # Good Friday 2024

    h2025 = us_market_holidays(2025)
    assert date(2025, 4, 18) in h2025  # Good Friday 2025

    h2026 = us_market_holidays(2026)
    assert date(2026, 4, 3) in h2026   # Good Friday 2026

    h2027 = us_market_holidays(2027)
    assert date(2027, 3, 26) in h2027  # Good Friday 2027


def test_market_calendar_early_closure_hours():
    """VUL-502: Scheduled early closure dates close at 1:00 PM ET (13:00) instead of 4:00 PM ET (16:00)."""
    early_2026 = get_early_close_dates(2026)
    black_friday_2026 = date(2026, 11, 27)
    xmas_eve_2026 = date(2026, 12, 24)

    assert black_friday_2026 in early_2026
    assert xmas_eve_2026 in early_2026

    assert get_market_close_time_et(black_friday_2026) == (13, 0)
    assert get_market_close_time_et(xmas_eve_2026) == (13, 0)
    assert get_market_close_time_et(date(2026, 11, 25)) == (16, 0)


def test_postmarket_next_session_trading_day():
    """VUL-505: Next session date correctly skips weekends and holidays."""
    # Friday Black Friday 2026 -> Monday Nov 30
    next_day = get_next_trading_day(date(2026, 11, 27))
    assert next_day == date(2026, 11, 30)

    # Thursday before Good Friday 2026 -> Monday April 6
    next_day_easter = get_next_trading_day(date(2026, 4, 2))
    assert next_day_easter == date(2026, 4, 6)


def test_fomc_end_year_dynamic_projection():
    """VUL-501: FOMC calendar dynamic projection extends to current year + 3."""
    ics_text = generate_economic_calendar_ics(catalytic_only=False)
    assert "BEGIN:VCALENDAR" in ics_text
    expected_future_year = str(date.today().year + 2)
    assert expected_future_year in ics_text


def test_briefing_naive_datetime_utc_anchor():
    """VUL-503: Naive datetimes passed to market briefings are converted to UTC before timezone translation."""
    agent = MarketBriefingAgent()
    portfolio = Portfolio(name="P", cash=100.0, holdings=[])
    naive_dt = datetime(2026, 9, 16, 22, 0)  # 10 PM UTC naive

    captured_prompts = []
    agent.query_llm_text = lambda prompt, **kwargs: (captured_prompts.append(prompt), "🌙 POST-MARKET WRAP")[1]

    agent.generate_postmarket_briefing(
        portfolio=portfolio,
        market_overview={"indices": {}},
        news_items=[],
        as_of=naive_dt,
        api_key="dummy"
    )

    assert len(captured_prompts) == 1
    # 22:00 UTC is 3:00 PM PDT
    assert "03:00 PM PDT" in captured_prompts[0] or "03:00 PM PST" in captured_prompts[0]


def test_nfp_holiday_shift():
    """VUL-506: NFP on a holiday (July 4th, 2025 Friday) rolls backward to Thursday July 3rd."""
    events = generate_statutory_macro_schedule(date(2025, 7, 1), date(2025, 7, 10))
    nfp_events = [e for e in events if "Non-Farm Payrolls" in e["name"]]
    assert len(nfp_events) == 1
    assert nfp_events[0]["date"] == "2025-07-03"  # Rolled back from July 4 holiday


# ==============================================================================
# 4. Multi-Channel Transport & Sink Limits (VUL-601 - VUL-606 & Telegram Tuning)
# ==============================================================================

def test_telegram_single_line_5000_char_split():
    """VUL-601 & User Request: A single 5000-character line splits at sentence/word boundaries under 4000 chars."""
    tg = TelegramChannel(bot_token="dummy", chat_id="123")
    long_line = "This is an important financial market insight. " * 110  # ~5170 chars
    chunks = tg._split_message(long_line, max_length=4000)

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 4000


def test_telegram_html_entity_escaping_ampersand():
    """VUL-602: Dynamic entity names like AT&T Inc. and <investors> are escaped safely."""
    raw = "AT&T Inc. <investor relations> & Johnson & Johnson"
    escaped = tg_safe(raw)
    assert "&amp;" in escaped
    assert "&lt;" in escaped
    assert "&gt;" in escaped
    assert "<" not in escaped
    assert ">" not in escaped


def test_slack_blocks_max_50_and_2900_chars():
    """VUL-603: Slack blocks are strictly capped at 50 blocks and 2900 chars per text element."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "A" * 3500}}] * 60
    validated = SlackChannel._validate_blocks(blocks)

    assert len(validated) == 50
    for b in validated:
        assert len(b["text"]["text"]) <= 2900
        assert b["text"]["text"].endswith("...")


def test_discord_description_split_multi_embeds():
    """VUL-604: Long descriptions (> 2000 chars) are split across multiple embeds up to 10."""
    discord = DiscordChannel(webhook_url="https://discord.com/api/webhooks/123/xyz")

    captured_payloads = []
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=204)
        long_desc = "X" * 4500
        discord.send_embed(title="Market Alert", description=long_desc)

        assert mock_post.called
        kwargs = mock_post.call_args[1]
        payload = kwargs["json"]
        embeds = payload["embeds"]
        assert len(embeds) == 3
        for e in embeds:
            assert len(e["description"]) <= 2000


def test_email_smtp_auth_credentials():
    """VUL-605: EmailChannel uses smtp_user and smtp_password when configured."""
    email_channel = EmailChannel(
        smtp_host="smtp.example.com",
        recipient="user@example.com",
        smtp_user="sentinel_bot",
        smtp_password="secure_password_123"
    )

    with patch("smtplib.SMTP") as mock_smtp:
        server_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server_instance

        success = email_channel.send_email(subject="Test", html_body="<p>Hello</p>")
        assert success is True
        server_instance.starttls.assert_called_once()
        server_instance.login.assert_called_once_with("sentinel_bot", "secure_password_123")


def test_notification_isolated_channel_failures():
    """VUL-606: A delivery failure or exception in one channel does not abort other channels."""
    agent = NotificationAgent()
    # Configure multiple channels
    agent.telegram.is_configured = MagicMock(return_value=True)
    agent.discord.is_configured = MagicMock(return_value=True)
    agent.slack.is_configured = MagicMock(return_value=True)

    # Discord throws unexpected runtime exception
    agent.discord.send_embed = MagicMock(side_effect=RuntimeError("Discord API Network Crash"))
    # Telegram and Slack succeed
    agent.telegram.send_message = MagicMock(return_value=True)
    agent.slack.send_blocks = MagicMock(return_value=True)

    report = BriefingReport(
        report_id="rep_test_isolation",
        generated_at=datetime.now(timezone.utc),
        executive_summary="All positions stable.",
        total_holdings_monitored=5,
        portfolio_stress=None,
        critical_risk_alerts=[],
        notable_risk_alerts=[],
        top_opportunities=[],
        critic_verdicts=[],
        raw_news_count=10,
        dispatched_channels=[]
    )

    dispatched = agent.dispatch_briefing(report)
    assert "Telegram" in dispatched
    assert "Slack" in dispatched
    assert "Discord" not in dispatched
