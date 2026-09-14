"""
Quantitative Risk & Portfolio Concentration Matrix.
Computes quantitative stress scenarios, sector weight distributions, and estimated beta exposures.
"""
from typing import Dict, Any

from models import Portfolio, PortfolioStressMetric


# Sector sensitivity mappings to macro factors (approximate beta & interest rate sensitivity)
SECTOR_MACRO_SENSITIVITIES = {
    "Technology": {"rate_sensitivity": -1.2, "oil_sensitivity": -0.2, "growth_beta": 1.3},
    "Semiconductors": {"rate_sensitivity": -1.1, "oil_sensitivity": -0.3, "growth_beta": 1.5},
    "Financials": {"rate_sensitivity": 0.8, "oil_sensitivity": 0.1, "growth_beta": 1.0},
    "Energy": {"rate_sensitivity": 0.1, "oil_sensitivity": 1.6, "growth_beta": 0.8},
    "Healthcare": {"rate_sensitivity": -0.3, "oil_sensitivity": -0.1, "growth_beta": 0.7},
    "Consumer Discretionary": {"rate_sensitivity": -0.9, "oil_sensitivity": -0.8, "growth_beta": 1.1},
    "Consumer Staples": {"rate_sensitivity": -0.2, "oil_sensitivity": -0.3, "growth_beta": 0.5},
    "Utilities": {"rate_sensitivity": -0.7, "oil_sensitivity": -0.2, "growth_beta": 0.4},
    "Industrials": {"rate_sensitivity": -0.4, "oil_sensitivity": -0.4, "growth_beta": 1.1},
    "Real Estate": {"rate_sensitivity": -1.5, "oil_sensitivity": -0.2, "growth_beta": 0.9},
}


class QuantRiskEngine:
    def __init__(self, max_sector_threshold_pct: float = 35.0):
        self.max_sector_threshold_pct = max_sector_threshold_pct

    def analyze_portfolio(self, portfolio: Portfolio) -> PortfolioStressMetric:
        portfolio.recalculate_weights()
        total_equity = portfolio.total_equity()

        if not portfolio.holdings or total_equity <= 0:
            return PortfolioStressMetric(
                sector_concentrations={},
                top_3_concentration_pct=0.0,
                high_concentration_warning=False,
                estimated_portfolio_beta=1.0,
                macro_shock_scenarios={}
            )

        # 1. Sector Concentrations & Herfindahl-Hirschman Index (HHI)
        sector_totals: Dict[str, float] = {}
        holdings_equity = sum(h.market_value for h in portfolio.holdings)
        for h in portfolio.holdings:
            sector = h.sector or "Unclassified"
            sector_totals[sector] = sector_totals.get(sector, 0.0) + h.market_value

        sector_concentrations = {
            sector: round((val / total_equity) * 100.0, 2)
            for sector, val in sector_totals.items()
        }

        # Sector HHI: sum of squared decimal weights in [0.0, 1.0] across equity holdings
        sector_hhi = round(sum((val / holdings_equity) ** 2 for val in sector_totals.values()), 4) if holdings_equity > 0 else 0.0


        # 2. Top-3 Concentration
        sorted_weights = sorted([h.weight_pct for h in portfolio.holdings], reverse=True)
        top_3_concentration = round(sum(sorted_weights[:3]), 2)

        has_high_concentration = any(
            weight >= self.max_sector_threshold_pct
            for weight in sector_concentrations.values()
        ) or top_3_concentration >= 60.0

        # 3. Estimated Weighted Beta & Sensitivities
        weighted_beta = 0.0
        weighted_rate_sensitivity = 0.0
        weighted_oil_sensitivity = 0.0

        for h in portfolio.holdings:
            w = h.weight_pct / 100.0
            sens = SECTOR_MACRO_SENSITIVITIES.get(h.sector, {"rate_sensitivity": -0.5, "oil_sensitivity": 0.0, "growth_beta": 1.0})
            weighted_beta += w * sens["growth_beta"]
            weighted_rate_sensitivity += w * sens["rate_sensitivity"]
            weighted_oil_sensitivity += w * sens["oil_sensitivity"]

        # 4. Volatility, VaR 95%, and Risk-Adjusted Return Metrics
        ann_vol = max(10.0, round(weighted_beta * 15.5 + 2.5, 2))
        daily_vol = (ann_vol / 100.0) / (252 ** 0.5)
        var_95_pct = round(1.645 * daily_vol * 100.0, 2)
        var_95_usd = round((var_95_pct / 100.0) * total_equity, 2)

        # Sharpe ratio is withheld (None) when no realized portfolio return time-series is available,
        # adhering strictly to data provenance and avoiding misleading affine-beta approximations.
        sharpe = None
        fields_unavailable = [
            "sharpe_ratio: Requires historical portfolio return time-series; synthetic affine beta mapping omitted"
        ]

        total_portfolio_wealth = total_equity + max(0.0, portfolio.cash)
        cash_pct = round((portfolio.cash / total_portfolio_wealth) * 100.0, 2) if total_portfolio_wealth > 0 else 0.0

        tech_semi_pct = sector_concentrations.get("Technology", 0) + sector_concentrations.get("Semiconductors", 0)

        # 5. Institutional Macro Shock & Stress Testing Scenarios
        macro_scenarios = {
            "+50 bps Fed Rate Spike": round(weighted_rate_sensitivity * 0.5 * 2.5, 2), # % portfolio impact
            "-50 bps Fed Rate Cut": round(-weighted_rate_sensitivity * 0.5 * 2.5, 2),
            "2022 Tech Rate Shock (-150 bps)": round(-0.14 * tech_semi_pct - 0.04 * (100 - tech_semi_pct), 2),
            "2008 GFC Liquidity Crisis": round(-20.0 * weighted_beta, 2),
            "AI Capex Pause (-15% Semis)": round(-0.15 * sector_concentrations.get("Semiconductors", 0) - 0.07 * sector_concentrations.get("Technology", 0), 2),
            "+20% Crude Oil Surge ($95/bbl)": round(weighted_oil_sensitivity * 0.2 * 10.0 - 1.2, 2),
            "Soft Landing & Broad S&P Rally": round(5.0 * weighted_beta + abs(weighted_rate_sensitivity) * 1.2, 2),
            "Broad Market 5% Correction": round(-5.0 * weighted_beta, 2),
        }

        return PortfolioStressMetric(
            sector_concentrations=sector_concentrations,
            top_3_concentration_pct=top_3_concentration,
            high_concentration_warning=has_high_concentration,
            sector_herfindahl_index=sector_hhi,
            estimated_portfolio_beta=round(weighted_beta, 2),
            annualized_volatility_pct=ann_vol,
            var_95_daily_pct=var_95_pct,
            var_95_daily_usd=var_95_usd,
            sharpe_ratio=sharpe,
            cash_allocation_pct=cash_pct,
            macro_shock_scenarios=macro_scenarios,
            fields_unavailable=fields_unavailable,
            provenance_note="Parametric sector-beta stress testing. Empirical returns required for realized Sharpe and CVaR."
        )


def compute_adv_liquidity_constraints(
    position_shares: float,
    current_price: float,
    median_adv_shares_30d: float,
    conviction_sized_usd: float,
    max_participation_rate: float = 0.10,
) -> Dict[str, Any]:
    """
    Computes institutional Average Daily Volume (ADV) turnover constraints.
    - Caps recommended maximum position size at 1% of 30-day median dollar turnover.
    - Calculates days to liquidate assuming a 10% volume participation ceiling.
    - Flags moonshot/opportunity recommendations if days_to_liquidate > 3 days.
    """
    position_usd = round(position_shares * current_price, 2)
    median_adv_usd = round(median_adv_shares_30d * current_price, 2) if median_adv_shares_30d > 0 else 0.0

    # 1% median ADV dollar turnover limit
    adv_cap_usd = round(0.01 * median_adv_usd, 2) if median_adv_usd > 0 else 0.0
    recommended_max_usd = min(conviction_sized_usd, adv_cap_usd) if adv_cap_usd > 0 else conviction_sized_usd

    # Days to liquidate under max 10% daily volume participation
    daily_participation_usd = max_participation_rate * median_adv_usd if median_adv_usd > 0 else 0.0
    days_to_liquidate = round(position_usd / daily_participation_usd, 2) if daily_participation_usd > 0 else 999.0

    return {
        "position_usd": position_usd,
        "median_adv_30d_usd": median_adv_usd,
        "adv_cap_usd": adv_cap_usd,
        "recommended_max_usd": round(recommended_max_usd, 2),
        "days_to_liquidate": days_to_liquidate,
        "exceeds_exit_horizon_gate": days_to_liquidate > 3.0
    }

