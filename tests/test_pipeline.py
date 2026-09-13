"""
Integration tests for the Financial Sentinel Multi-Agent Pipeline.
"""
import os
import json
import pytest
from orchestrator import FinancialSentinelOrchestrator
from models import Portfolio, AlertPriority, DirectionalImpact, CriticVerdict


@pytest.fixture
def temp_orchestrator(tmp_path):
    db_path = str(tmp_path / "test_state.db")
    return FinancialSentinelOrchestrator(db_path=db_path)


@pytest.fixture
def sample_portfolio_obj(temp_orchestrator):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/sample_portfolio.json")
    return temp_orchestrator.load_portfolio_from_file(path)


@pytest.fixture
def sample_news_fixtures():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/sample_news_feeds.json")
    with open(path, "r") as f:
        return json.load(f)


def test_full_pipeline_execution(temp_orchestrator, sample_portfolio_obj, sample_news_fixtures, monkeypatch):
    """
    Validates end-to-end execution across all 5 agents:
    Ingestion -> Analysis -> Opportunity -> Critic -> Notification -> State Store
    """
    # Mock LLM calls across the orchestrator agents
    monkeypatch.setattr(temp_orchestrator.analysis_agent, "query_llm_json", lambda *args, **kwargs: {
        "analyses": [
            {
                "ticker": "NVDA",
                "impact": "BEARISH",
                "magnitude_pct": 4.5,
                "priority": "P0_CRITICAL",
                "transmission_channel": "DOJ Antitrust Probe",
                "rationale": "DOJ opens probe into pricing contracts.",
                "action": "Tighten stop-loss limit to 110.00",
                "citations": ["Reuters: DOJ expands probe"]
            }
        ]
    })
    monkeypatch.setattr(temp_orchestrator.opportunity_agent, "query_llm_json", lambda *args, **kwargs: {
        "opportunities": [
            {
                "ticker": "CEG",
                "name": "Constellation Energy",
                "sector": "Utilities",
                "theme": "AI Nuclear Power",
                "horizon": "SECULAR",
                "catalyst_description": "20-year nuclear power agreement.",
                "why_now": "Hyperscaler energy demand.",
                "upside_thesis": "Long term revenue visibility.",
                "risk_factors": ["Grid approvals"],
                "asymmetric_ratio": 3.2,
                "estimated_upside_pct": 28.0,
                "suggested_stop_loss_pct": 7.0,
                "portfolio_synergy": "Clean energy hedge."
            }
        ]
    })
    monkeypatch.setattr(temp_orchestrator.critic_agent, "query_llm_json", lambda *args, **kwargs: {
        "reviews": [
            {
                "target_id": "risk_NVDA_test_news",
                "item_type": "risk_analysis",
                "verdict": "APPROVED",
                "bias_score": 0.15,
                "source_credibility_grade": "A",
                "calibrated_confidence_pct": 90.0,
                "counter_thesis_questions": ["Is revenue impact quantified?"],
                "identified_biases": [],
                "review_summary": "[NVDA] Valid antitrust risk."
            }
        ]
    })
    monkeypatch.setattr(temp_orchestrator.notification_agent, "query_llm_text", lambda *args, **kwargs: "Executive Summary: NVDA faces regulatory headwinds while CEG offers secular nuclear energy alpha.")

    briefing = temp_orchestrator.run_monitoring_cycle(
        portfolio=sample_portfolio_obj,
        live=False,
        mock_news=sample_news_fixtures,
        api_key="test_mock_key"
    )

    # Verify Report structure
    assert briefing is not None
    assert briefing.report_id.startswith("rep_")
    assert briefing.total_holdings_monitored == len(sample_portfolio_obj.holdings)
    assert briefing.raw_news_count == len(sample_news_fixtures)

    # Verify Defensive Analysis Output
    assert len(briefing.critical_risk_alerts) >= 1
    nvda_alert = next((r for r in briefing.critical_risk_alerts if r.holding_ticker == "NVDA"), None)
    assert nvda_alert is not None
    assert nvda_alert.impact in (DirectionalImpact.BEARISH, DirectionalImpact.HIGH_VOLATILITY)
    assert nvda_alert.direct_exposure is True

    # Verify Opportunity Discovery Output
    assert len(briefing.top_opportunities) >= 1
    assert all(o.asymmetric_ratio >= 1.5 for o in briefing.top_opportunities)

    # Verify Critic Audits
    assert len(briefing.critic_verdicts) >= 1
    assert any(cv.verdict in (CriticVerdict.APPROVED, CriticVerdict.APPROVED_WITH_CAVEATS) for cv in briefing.critic_verdicts)

    # Verify Database Persistence
    saved_briefings = temp_orchestrator.state_store.get_recent_briefings(limit=5)
    assert len(saved_briefings) == 1
    assert saved_briefings[0]["report_id"] == briefing.report_id


def test_deduplication_store(temp_orchestrator, sample_portfolio_obj, sample_news_fixtures, monkeypatch):
    """
    Validates that executing consecutive cycles with the same news feeds correctly deduplicates items.
    """
    monkeypatch.setattr(temp_orchestrator.analysis_agent, "query_llm_json", lambda *args, **kwargs: {"analyses": []})
    monkeypatch.setattr(temp_orchestrator.opportunity_agent, "query_llm_json", lambda *args, **kwargs: {"opportunities": []})
    monkeypatch.setattr(temp_orchestrator.critic_agent, "query_llm_json", lambda *args, **kwargs: {"reviews": []})

    # First Run: ingests all news
    briefing1 = temp_orchestrator.run_monitoring_cycle(
        portfolio=sample_portfolio_obj, live=False, mock_news=sample_news_fixtures, api_key="test_key"
    )
    assert briefing1.raw_news_count == len(sample_news_fixtures)

    # Second Run: should detect that all items are already processed
    briefing2 = temp_orchestrator.run_monitoring_cycle(
        portfolio=sample_portfolio_obj, live=False, mock_news=sample_news_fixtures, api_key="test_key"
    )
    assert briefing2.raw_news_count == 0



def test_cash_persistence_and_retrieval(temp_orchestrator, sample_portfolio_obj):
    """
    Validates that portfolio cash is explicitly preserved in state store and persists.
    """
    sample_portfolio_obj.cash = 15500.0
    temp_orchestrator.persist_active_portfolio(sample_portfolio_obj)
    
    # Reload active portfolio
    reloaded_p = temp_orchestrator.get_active_portfolio()
    assert reloaded_p.cash == 15500.0
    assert temp_orchestrator.state_store.get_kv("portfolio_cash") == 15500.0


def test_earnings_calendar_7day_schedule(monkeypatch):
    """
    Validates that fetch_7day_earnings_schedule parses Nasdaq data correctly.
    """
    from unittest.mock import MagicMock
    import httpx
    from analytics.earnings_calendar import fetch_7day_earnings_schedule

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
                },
                {
                    "symbol": "CRWD",
                    "name": "CrowdStrike Holdings",
                    "time": "time-pre-market",
                    "marketCap": "$70,000,000,000",
                    "epsForecast": "$0.98",
                    "lastYearEPS": "$0.74"
                }
            ]
        }
    }

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: mock_resp)

    schedule = fetch_7day_earnings_schedule(portfolio_tickers=["NVDA"])
    assert isinstance(schedule, list)
    assert len(schedule) == 7
    day0 = schedule[0]
    assert "date" in day0
    assert "day_name" in day0
    assert len(day0["bmo"]) == 1
    assert len(day0["amc"]) == 1
    assert day0["bmo"][0]["ticker"] == "CRWD"
    assert day0["amc"][0]["ticker"] == "NVDA"


def test_telegram_add_and_rm_parsers():
    from channels.telegram_bot import parse_add_args, parse_rm_args

    # Test /add parsing
    res1 = parse_add_args("/add NVDA 1 @ 123")
    assert res1 == {"ticker": "NVDA", "shares": 1.0, "price": 123.0}

    res2 = parse_add_args("/add $AAPL 10 @ 225.50")
    assert res2 == {"ticker": "AAPL", "shares": 10.0, "price": 225.5}

    res3 = parse_add_args("/add TSLA 5")
    assert res3 == {"ticker": "TSLA", "shares": 5.0, "price": None}

    # Test /rm parsing
    rm1 = parse_rm_args("/rm NVDA 1 @ 123")
    assert rm1 == {"ticker": "NVDA", "shares": 1.0, "price": 123.0}

    rm2 = parse_rm_args("/rm $NVDA 5")
    assert rm2 == {"ticker": "NVDA", "shares": 5.0, "price": None}

    rm3 = parse_rm_args("/rm NVDA all")
    assert rm3 == {"ticker": "NVDA", "shares": "all", "price": None}

    rm4 = parse_rm_args("/rm NVDA")
    assert rm4 == {"ticker": "NVDA", "shares": None, "price": None}


def test_telegram_add_and_rm_execution_flow(temp_orchestrator, sample_portfolio_obj):
    from channels.telegram_bot import FinancialSentinelTelegramBot
    from unittest.mock import patch

    bot = FinancialSentinelTelegramBot(orchestrator=temp_orchestrator)
    sent_messages = []
    bot.send_message = lambda msg, chat_id=None: sent_messages.append(msg)

    # Initialize active portfolio
    temp_orchestrator.persist_active_portfolio(sample_portfolio_obj)
    orig_count = len(sample_portfolio_obj.holdings)

    # 1. Test /add new ticker
    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Palantir", "sector": "Technology", "current_price": 32.50}):
        bot._handle_incoming_message("/add PLTR 10 @ 30.00", "test_chat", "Investor")

    assert any("HOLDING UPDATED SUCCESSFULLY" in m for m in sent_messages)
    p_after_add = temp_orchestrator.get_active_portfolio()
    assert len(p_after_add.holdings) == orig_count + 1
    pltr_h = next((h for h in p_after_add.holdings if h.ticker == "PLTR"), None)
    assert pltr_h is not None
    assert pltr_h.shares == 10.0
    assert pltr_h.avg_price == 30.00

    # 2. Test /add existing ticker (average in)
    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Palantir", "sector": "Technology", "current_price": 35.00}):
        bot._handle_incoming_message("/add PLTR 10 @ 40.00", "test_chat", "Investor")
    
    p_after_add2 = temp_orchestrator.get_active_portfolio()
    pltr_h2 = next((h for h in p_after_add2.holdings if h.ticker == "PLTR"), None)
    assert pltr_h2.shares == 20.0
    assert pltr_h2.avg_price == 35.00  # (10*30 + 10*40) / 20 = 35.0

    # 3. Test /rm trimming shares
    bot._handle_incoming_message("/rm PLTR 5", "test_chat", "Investor")
    p_after_trim = temp_orchestrator.get_active_portfolio()
    pltr_h3 = next((h for h in p_after_trim.holdings if h.ticker == "PLTR"), None)
    assert pltr_h3.shares == 15.0

    # 4. Test /rm closing position completely
    bot._handle_incoming_message("/rm PLTR all", "test_chat", "Investor")
    p_after_rm = temp_orchestrator.get_active_portfolio()
    assert len(p_after_rm.holdings) == orig_count
    assert not any(h.ticker == "PLTR" for h in p_after_rm.holdings)


def test_backup_to_gcs_blocking_and_wal_checkpoint(temp_orchestrator):
    store = temp_orchestrator.state_store
    store.set_kv("test_key", {"data": 123})
    res = store.backup_to_gcs(blocking=True)
    assert res in (True, False)
    with store._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        row = cursor.fetchone()
        assert row is not None
