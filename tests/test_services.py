"""
tests/test_services.py - Unit tests for Phase 2 Domain Services.
Tests PortfolioService, AnalysisService, IdentityService, and BriefingService in isolation.
"""
import pytest
from unittest.mock import patch
from models import Portfolio, PortfolioHolding, SingleTickerAnalysis
from orchestrator import FinancialSentinelOrchestrator
from services.identity_service import IdentityService
from services.portfolio_service import PortfolioService
from services.analysis_service import AnalysisService
from services.briefing_service import BriefingService


@pytest.fixture
def service_env(tmp_path):
    db_path = str(tmp_path / "test_services.db")
    orch = FinancialSentinelOrchestrator(db_path=db_path)
    admin = orch.state_store.get_or_create_default_admin()

    sample_p = Portfolio(
        name="Test Portfolio",
        cash=10000.0,
        holdings=[
            PortfolioHolding(
                ticker="AAPL",
                name="Apple Inc.",
                shares=10.0,
                avg_price=200.0,
                current_price=220.0,
                sector="Technology",
                thematic_tags=[]
            )
        ]
    )
    orch.persist_active_portfolio(sample_p, user_id=admin["id"])

    portfolio_svc = PortfolioService(orchestrator=orch)
    analysis_svc = AnalysisService(orchestrator=orch)
    briefing_svc = BriefingService(orchestrator=orch)
    identity_svc = IdentityService(state_store=orch.state_store)

    return {
        "orch": orch,
        "admin": admin,
        "portfolio_svc": portfolio_svc,
        "analysis_svc": analysis_svc,
        "briefing_svc": briefing_svc,
        "identity_svc": identity_svc,
    }


def test_portfolio_service_valuation_and_cash(service_env):
    p_svc = service_env["portfolio_svc"]
    admin = service_env["admin"]

    val = p_svc.get_portfolio_valuation(user_id=admin["id"], update_prices=False)
    assert val["holdings_count"] == 1
    assert val["stock_equity"] == 2200.0
    assert val["cash"] == 10000.0
    assert val["total_equity"] == 12200.0
    assert val["total_wealth"] == 12200.0
    assert val["top_performer"].ticker == "AAPL"

    # Test cash update
    res = p_svc.update_cash_balance(user_id=admin["id"], new_cash=15000.0)
    assert res["status"] == "success"
    assert res["cash"] == 15000.0
    reloaded = p_svc.get_portfolio(user_id=admin["id"])
    assert reloaded.cash == 15000.0


def test_portfolio_service_add_incremental_averaging(service_env):
    p_svc = service_env["portfolio_svc"]
    admin = service_env["admin"]

    # Initial: 10 shares of AAPL @ $200.00
    # Add 10 shares @ $240.00 incrementally -> 20 shares @ $220.00 avg basis
    res = p_svc.add_or_update_holding(
        user_id=admin["id"],
        ticker="AAPL",
        shares=10.0,
        price=240.0,
        incremental=True,
        deduct_cash=True
    )
    assert res["status"] == "success"
    assert res["action"] == "updated"
    assert res["delta_cash"] == -2400.0

    p = p_svc.get_portfolio(user_id=admin["id"])
    aapl = next(h for h in p.holdings if h.ticker == "AAPL")
    assert aapl.shares == 20.0
    assert aapl.avg_price == 220.0
    assert p.cash == 7600.0  # 10000 - 2400


def test_portfolio_service_add_new_position(service_env):
    p_svc = service_env["portfolio_svc"]
    admin = service_env["admin"]

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "NVIDIA", "sector": "Semiconductors", "current_price": 120.0}):
        res = p_svc.add_or_update_holding(
            user_id=admin["id"],
            ticker="NVDA",
            shares=5.0,
            price=120.0,
            incremental=False,
            deduct_cash=True
        )

    assert res["status"] == "success"
    assert res["action"] == "created"
    p = p_svc.get_portfolio(user_id=admin["id"])
    assert len(p.holdings) == 2
    nvda = next(h for h in p.holdings if h.ticker == "NVDA")
    assert nvda.shares == 5.0
    assert nvda.avg_price == 120.0
    assert p.cash == 9400.0  # 10000 - 600


def test_portfolio_service_trim_and_remove(service_env):
    p_svc = service_env["portfolio_svc"]
    admin = service_env["admin"]

    # Trim 4 shares of AAPL (current price 220.0) -> credits 4 * 220 = 880 to cash
    trim_res = p_svc.remove_or_trim_holding(
        user_id=admin["id"],
        ticker="AAPL",
        shares_to_remove=4.0,
        credit_cash=True
    )
    assert trim_res["status"] == "success"
    assert not trim_res["is_full_removal"]
    assert trim_res["liquidated_val"] == 880.0
    assert trim_res["remaining_shares"] == 6.0

    p = p_svc.get_portfolio(user_id=admin["id"])
    assert p.cash == 10880.0

    # Liquidate remaining shares
    rm_res = p_svc.remove_or_trim_holding(
        user_id=admin["id"],
        ticker="AAPL",
        shares_to_remove="all",
        credit_cash=True
    )
    assert rm_res["status"] == "success"
    assert rm_res["is_full_removal"]
    assert rm_res["liquidated_val"] == 6.0 * 220.0

    p_empty = p_svc.get_portfolio(user_id=admin["id"])
    assert len(p_empty.holdings) == 0
    assert p_empty.cash == 10880.0 + (6.0 * 220.0)

    # Re-removing should raise ValueError
    with pytest.raises(ValueError, match="not found in portfolio"):
        p_svc.remove_or_trim_holding(user_id=admin["id"], ticker="AAPL")


def test_identity_service_routing(service_env):
    id_svc = service_env["identity_svc"]
    admin = service_env["admin"]

    # Test key resolution
    key = id_svc.resolve_api_key(admin["id"])
    assert key is not None

    # Test telegram resolution
    user = id_svc.resolve_user_from_telegram(username="investor")
    assert user is not None
    assert user["id"] == admin["id"]

    # Test link chat id
    id_svc.link_telegram_chat_id(admin["id"], "998877")
    reloaded = service_env["orch"].state_store.get_user_by_id(admin["id"])
    assert reloaded["telegram_chat_id"] == "998877"


def test_analysis_service_execution_and_provenance(service_env):
    analysis_svc = service_env["analysis_svc"]
    orch = service_env["orch"]
    admin = service_env["admin"]

    progress_events = []

    def on_progress(pct, msg):
        progress_events.append((pct, msg))

    mock_analysis = SingleTickerAnalysis(
        ticker="MSFT",
        company_name="Microsoft",
        verdict="BULLISH",
        conviction_score=88.0,
        thesis="Cloud and AI infrastructure momentum.",
        telegram_html="🔬 <b>STOCK ANALYSIS: MSFT</b>"
    )

    from analytics.technical_indicators import TechnicalSnapshot
    mock_tech = TechnicalSnapshot(ticker="MSFT", current_price=430.0, rsi_14=58.5, macd_line=2.1, atr_14=5.2, is_live=True)

    with patch("analytics.market_data.fetch_live_quote", return_value={"name": "Microsoft", "current_price": 430.0, "sector": "Technology"}), \
         patch("analytics.technical_indicators.compute_technical_snapshot", return_value=mock_tech), \
         patch.object(orch.analysis_agent, "analyze_single_ticker_structured", return_value=mock_analysis):

        res = analysis_svc.run_single_ticker_analysis(
            ticker="MSFT",
            user_id=admin["id"],
            api_key="mock_key",
            progress_callback=on_progress
        )

    assert res["status"] == "success"
    assert res["ticker"] == "MSFT"
    assert res["verdict"] == "BULLISH"
    assert res["conviction_score"] == 88.0
    assert res["deepdive_id"] is not None
    assert [p[0] for p in progress_events] == [20, 40, 60, 80, 100]

    # Verify auto-archived in state_store
    dd = orch.state_store.get_deepdive_by_id(res["deepdive_id"])
    assert dd is not None
    assert dd["ticker"] == "MSFT"
    assert dd["current_price"] == 430.0


def test_analysis_service_unrecognized_ticker_rejection(service_env):
    analysis_svc = service_env["analysis_svc"]
    admin = service_env["admin"]

    with patch("analytics.market_data.fetch_live_quote", return_value={"current_price": 0.0}):
        with pytest.raises(ValueError, match="not recognized"):
            analysis_svc.run_single_ticker_analysis(
                ticker="UNKNOWNXYZ",
                user_id=admin["id"],
                api_key="mock_key"
            )


def test_briefing_service_lifecycle(service_env):
    b_svc = service_env["briefing_svc"]
    admin = service_env["admin"]
    orch = service_env["orch"]

    # Record test briefing
    orch.state_store.record_market_briefing(
        briefing_id="briefing_test_101",
        slot="premarket",
        message="Test premarket memo",
        user_id=admin["id"],
        dispatched_channels=[]
    )

    briefings = b_svc.list_briefings(user_id=admin["id"], slot="premarket")
    assert len(briefings) >= 1
    assert briefings[0]["report_id"] == "briefing_test_101"

    detail = b_svc.get_briefing("briefing_test_101")
    assert detail is not None
    assert detail["message_html"] == "Test premarket memo"

    # Test dispatch
    with patch.object(b_svc.telegram, "send_message") as mock_send:
        success = b_svc.dispatch_briefing("briefing_test_101", chat_id="123456")
        assert success is True
        assert mock_send.called
