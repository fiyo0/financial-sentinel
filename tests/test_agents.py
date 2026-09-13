"""
Unit tests for individual agents in isolation.
"""
import pytest
from models import (
    Portfolio, PortfolioHolding, HoldingExposureAnalysis, OpportunityAnalysis, OpportunityHorizon,
    DirectionalImpact, AlertPriority, CriticVerdict
)
from agents.news_ingestion import NewsIngestionAgent
from agents.analysis_agent import PortfolioAnalysisAgent
from agents.opportunity_agent import OpportunityDiscoveryAgent
from agents.critic_agent import RiskCriticAgent
from agents.notification_agent import NotificationAgent
from storage.state_store import StateStore


@pytest.fixture
def mock_portfolio():
    return Portfolio(
        name="Test Portfolio",
        cash=5000.0,
        holdings=[
            PortfolioHolding(
                ticker="AAPL",
                name="Apple Inc.",
                shares=50,
                avg_price=190.0,
                current_price=220.0,
                sector="Technology",
                thematic_tags=["Consumer Tech"]
            ),
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA Corporation",
                shares=100,
                avg_price=100.0,
                current_price=125.0,
                sector="Semiconductors",
                thematic_tags=["AI"]
            )
        ]
    )


def test_news_ingestion_entity_extraction(tmp_path):
    store = StateStore(str(tmp_path / "test.db"))
    agent = NewsIngestionAgent(store)

    text = "Federal Reserve issues statement on inflation while $NVDA and TSMC (TSM) announce joint AI foundry."
    entities = agent.extract_entities(text)

    assert "NVDA" in entities["tickers"]
    assert "TSM" in entities["tickers"]
    assert "Semiconductors" in entities["sectors"] or "Technology" in entities["sectors"]


def test_analysis_agent_supply_chain_ripple(mock_portfolio, monkeypatch):
    agent = PortfolioAnalysisAgent()
    monkeypatch.setattr(agent, "query_llm_json", lambda *args, **kwargs: {
        "analyses": [
            {
                "ticker": "NVDA",
                "impact": "BEARISH",
                "magnitude_pct": 4.5,
                "priority": "P0_CRITICAL",
                "transmission_channel": "Supply Chain Bottleneck",
                "rationale": "TSMC wafer packaging halt directly impacts NVDA GPU assembly lines.",
                "action": "Tighten trailing stop-loss.",
                "citations": ["Reuters: TSMC Packaging Halt"]
            }
        ]
    })

    analyses = agent.analyze_news_against_portfolio([], mock_portfolio, api_key="test_api_key")
    assert len(analyses) >= 1
    assert analyses[0].holding_ticker == "NVDA"
    assert analyses[0].impact == DirectionalImpact.BEARISH


def test_opportunity_agent_thematic_discovery(mock_portfolio, monkeypatch):
    agent = OpportunityDiscoveryAgent()
    monkeypatch.setattr(agent, "query_llm_json", lambda *args, **kwargs: {
        "opportunities": [
            {
                "ticker": "CEG",
                "name": "Constellation Energy",
                "sector": "Utilities",
                "theme": "AI Nuclear Power",
                "horizon": "SECULAR",
                "catalyst_description": "20-year nuclear deal for datacenter power.",
                "why_now": "Hyperscaler energy demand surge.",
                "upside_thesis": "Multi-year contracted revenue base.",
                "risk_factors": ["Regulatory approvals"],
                "asymmetric_ratio": 3.2,
                "estimated_upside_pct": 28.0,
                "suggested_stop_loss_pct": 7.0,
                "portfolio_synergy": "Adds non-tech clean energy exposure."
            }
        ]
    })

    opps = agent.scan_opportunities([], mock_portfolio, api_key="test_api_key")
    assert len(opps) >= 1
    ceg_opp = opps[0]
    assert ceg_opp.ticker == "CEG"
    assert ceg_opp.horizon == OpportunityHorizon.SECULAR
    assert ceg_opp.asymmetric_ratio > 2.0


def test_critic_agent_hype_detection():
    agent = RiskCriticAgent()

    # Highly speculative hyped opportunity
    hyped_opp = OpportunityAnalysis(
        ticker="MEME",
        name="Meme Rocket Inc",
        sector="Technology",
        theme="Viral Momentum",
        horizon=OpportunityHorizon.TACTICAL,
        catalyst_description="Anonymous source says stock will go to the moon with guaranteed 10x return!",
        why_now="Viral trending stock on forums",
        upside_thesis="Guaranteed massive short squeeze and revolutionary breakthrough.",
        risk_factors=[],
        asymmetric_ratio=10.0,
        estimated_upside_pct=500.0,
        suggested_stop_loss_pct=2.0,
        citations=["forum_post"],
        news_item_id="news_meme_1"
    )

    review = agent.review_opportunity(hyped_opp)
    assert review.verdict in (CriticVerdict.REJECTED_SPECULATIVE, CriticVerdict.APPROVED_WITH_CAVEATS)
    assert len(review.identified_biases) > 0


def test_notification_agent_channel_formatting(mock_portfolio):
    agent = NotificationAgent()

    sample_risk = HoldingExposureAnalysis(
        holding_ticker="NVDA",
        holding_name="NVIDIA Corporation",
        impact=DirectionalImpact.BEARISH,
        impact_magnitude_pct=4.5,
        priority=AlertPriority.P0_CRITICAL,
        direct_exposure=True,
        transmission_channel="Antitrust Subpoena",
        rationale="DOJ opens probe into pricing contracts.",
        key_risks=["Regulatory fines", "Multiple compression"],
        recommended_action="Tighten stop-loss limit to 110.00",
        citations=["Reuters: DOJ expands probe"],
        news_item_id="news_1"
    )

    briefing = agent.generate_briefing(
        risk_analyses=[sample_risk],
        opportunities=[],
        critic_reviews=[],
        total_holdings_monitored=2,
        raw_news_count=1
    )

    tg_text = agent.format_telegram_message(briefing)
    assert "CRITICAL RISK ALERTS (P0)" in tg_text
    assert "NVDA" in tg_text
    assert any(tz_label in tg_text for tz_label in ["PST", "PDT"])

    email_html = agent.format_html_email(briefing)
    assert "Financial Sentinel Briefing" in email_html
    assert "NVDA" in email_html
    assert any(tz_label in email_html for tz_label in ["PST", "PDT"])


def test_single_ticker_analysis_structured(mock_portfolio, monkeypatch):
    agent = PortfolioAnalysisAgent()
    mock_json = {
        "verdict": "BULLISH",
        "conviction_score": 88.0,
        "thesis": "Secular datacenter demand and proprietary CUDA moat provide strong pricing power.",
        "catalysts": [
            "Blackwell architecture volume ramp driving enterprise datacenter compute expansion.",
            "Networking and Spectrum-X Ethernet attach rates accelerating gross margin resilience."
        ],
        "risks": [
            "Export control restrictions limiting addressable revenue in key geographic markets.",
            "Customer capex digestion risk following massive hyperscaler buildout cycles."
        ],
        "target_price": 165.0,
        "stop_floor": 115.0,
        "suggested_allocation_usd": 1500.0,
        "telegram_html": (
            "🔬 <b>STOCK ANALYSIS: NVDA (NVIDIA Corporation)</b>\n"
            "<i>Sector: Semiconductors | Price: $125.50</i>\n\n"
            "📊 <b>Fundamental Catalysts & Growth Drivers:</b>\n"
            "• <b>Blackwell GPU Compute Cycle:</b> Multi-quarter hyperscaler order backlogs.\n\n"
            "💼 <b>Portfolio Fit & Synergy Analysis:</b>\n"
            "Complementary to existing compute exposure.\n\n"
            "⚠️ <b>Key Risks & Fundamental Vulnerabilities:</b>\n"
            "• <b>Supply Chain Packaging:</b> CoWoS advanced packaging capacity constraints.\n\n"
            "🎯 <b>Conviction Verdict & Actionable Sizing:</b>\n"
            "• <b>Verdict:</b> 🟢 <b>BULLISH (ACCUMULATE)</b>\n"
            "• <b>Verdict Rationale:</b> Robust multi-sentence synthesis of momentum, margin expansion, and demand.\n"
            "• <b>Target Price & Trailing Stop:</b> Target: $165.00 | Dynamic Stop: $115.00\n"
            "• <b>Position Sizing:</b> Deploy $1,500.00 from cash reserves (~12 shares)."
        )
    }
    monkeypatch.setattr(agent, "query_llm_json", lambda *args, **kwargs: mock_json)

    quote = {
        "ticker": "NVDA",
        "name": "NVIDIA Corporation",
        "current_price": 125.50,
        "sector": "Semiconductors"
    }

    analysis = agent.analyze_single_ticker_structured(
        ticker="NVDA",
        portfolio=mock_portfolio,
        news_items=[],
        quote_data=quote,
        api_key="mock_byok_key"
    )

    assert analysis.ticker == "NVDA"
    assert analysis.verdict == "BULLISH"
    assert analysis.conviction_score == 88.0
    assert len(analysis.catalysts) == 2
    assert len(analysis.risks) == 2
    assert analysis.target_price == 165.0
    assert analysis.stop_floor == 115.0
    assert analysis.suggested_allocation_usd == 1500.0
    assert "Fundamental Catalysts & Growth Drivers" in analysis.telegram_html
    assert "Portfolio Fit & Synergy Analysis" in analysis.telegram_html
    assert "Conviction Verdict & Actionable Sizing" in analysis.telegram_html

    # Also verify backward-compatible analyze_single_ticker wrapper
    raw_html = agent.analyze_single_ticker(
        ticker="NVDA",
        portfolio=mock_portfolio,
        news_items=[],
        quote_data=quote,
        api_key="mock_byok_key"
    )
    assert raw_html == analysis.telegram_html





