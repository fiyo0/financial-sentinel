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
    assert stress.sector_herfindahl_index == 1.0  # 100% in Technology
    assert stress.sharpe_ratio is None  # Circular Sharpe removed; requires empirical return series
    assert any("sharpe_ratio" in field for field in stress.fields_unavailable)
    assert stress.cash_allocation_pct > 0.0
    assert "2008 GFC Liquidity Crisis" in stress.macro_shock_scenarios
    assert "AI Capex Pause (-15% Semis)" in stress.macro_shock_scenarios

    # Multi-sector HHI test: equal weights across 2 sectors -> 0.5^2 + 0.5^2 = 0.5
    portfolio.holdings.append(
        PortfolioHolding(
            ticker="JPM",
            name="JPMorgan Chase",
            shares=50,
            avg_price=200.0,
            current_price=200.0,
            sector="Financials"
        )
    )
    multi_stress = engine.analyze_portfolio(portfolio)
    assert multi_stress.sector_herfindahl_index == 0.5


def test_adv_liquidity_constraints():
    from analytics.quant_risk import compute_adv_liquidity_constraints

    # Position: 1,000 shares @ $100 = $100,000
    # 30-day median ADV: 50,000 shares = $5,000,000 turnover
    # 1% turnover cap: $50,000
    # At 10% daily participation ($500,000/day), days to liquidate = 100k / 500k = 0.2 days
    res = compute_adv_liquidity_constraints(
        position_shares=1000,
        current_price=100.0,
        median_adv_shares_30d=50000,
        conviction_sized_usd=75000.0,
        max_participation_rate=0.10
    )
    assert res["position_usd"] == 100000.0
    assert res["median_adv_30d_usd"] == 5000000.0
    assert res["adv_cap_usd"] == 50000.0
    assert res["recommended_max_usd"] == 50000.0  # Capped by 1% ADV turnover
    assert res["days_to_liquidate"] == 0.2
    assert not res["exceeds_exit_horizon_gate"]

    # Illiquid position exceeding 3 days exit horizon:
    # 30-day median ADV: 2,000 shares = $200,000 turnover
    # At 10% participation ($20,000/day), 100k position takes 5.0 days to liquidate
    illiquid = compute_adv_liquidity_constraints(
        position_shares=1000,
        current_price=100.0,
        median_adv_shares_30d=2000,
        conviction_sized_usd=75000.0,
        max_participation_rate=0.10
    )
    assert illiquid["days_to_liquidate"] == 5.0
    assert illiquid["exceeds_exit_horizon_gate"]

