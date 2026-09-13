"""
Unit tests for the Quantitative Risk & Macro Stress Matrix engine.
"""
from models import Portfolio, PortfolioHolding
from analytics.quant_risk import QuantRiskEngine


def test_quant_risk_concentration_detection():
    engine = QuantRiskEngine(max_sector_threshold_pct=35.0)

    # Heavily concentrated Tech portfolio
    portfolio = Portfolio(
        name="Concentrated Tech Portfolio",
        cash=0.0,
        holdings=[
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA",
                shares=100,
                avg_price=100.0,
                current_price=130.0,
                sector="Semiconductors"
            ),
            PortfolioHolding(
                ticker="AAPL",
                name="Apple",
                shares=100,
                avg_price=180.0,
                current_price=220.0,
                sector="Technology"
            ),
            PortfolioHolding(
                ticker="MSFT",
                name="Microsoft",
                shares=50,
                avg_price=400.0,
                current_price=420.0,
                sector="Technology"
            )
        ]
    )

    stress = engine.analyze_portfolio(portfolio)

    assert stress.high_concentration_warning is True
    assert stress.sector_concentrations.get("Technology", 0) > 35.0 or stress.top_3_concentration_pct >= 60.0
    assert stress.estimated_portfolio_beta > 1.0
    assert "+50 bps Fed Rate Spike" in stress.macro_shock_scenarios
    assert stress.macro_shock_scenarios["+50 bps Fed Rate Spike"] < 0  # High tech is negative rate sensitivity


def test_quant_risk_institutional_metrics():
    engine = QuantRiskEngine()
    portfolio = Portfolio(
        name="Balanced Portfolio",
        cash=5000.0,
        holdings=[
            PortfolioHolding(
                ticker="VOO",
                name="Vanguard S&P 500 ETF",
                shares=20,
                avg_price=450.0,
                current_price=500.0,
                sector="Technology"
            )
        ]
    )

    stress = engine.analyze_portfolio(portfolio)
    assert stress.annualized_volatility_pct > 0.0
    assert stress.var_95_daily_pct > 0.0
    assert stress.var_95_daily_usd > 0.0
    assert stress.sharpe_ratio > 0.0
    assert stress.cash_allocation_pct > 0.0
    assert "2008 GFC Liquidity Crisis" in stress.macro_shock_scenarios
    assert "AI Capex Pause (-15% Semis)" in stress.macro_shock_scenarios
