"""
Quantitative Risk & Portfolio Concentration Matrix.
Computes quantitative stress scenarios, sector weight distributions, and estimated beta exposures.
"""
from typing import Dict, Any, List, Optional, Tuple

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
            "sharpe_ratio: Requires historical portfolio return time-series; synthetic affine beta mapping omitted",
            "annualized_volatility_pct: Derived from static SECTOR_MACRO_SENSITIVITIES lookup, not from a realized return series. Not a statistical volatility estimate.",
            "var_95_daily_pct / var_95_daily_usd: Inherits the synthetic volatility above. Indicative scale only; not a validated risk measure.",
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


def apply_data_quality_ceiling(
    conviction_pct: float,
    tech_snapshot: Optional[Any] = None,
    stress_metrics: Optional[Any] = None,
    missing_technical_fields: Optional[List[str]] = None,
) -> Tuple[float, List[str]]:
    """
    Enforces deterministic data quality ceilings on investment conviction scores (R-4).
    - If core technical indicators (RSI-14, MACD, or ATR-14) are missing/unavailable, conviction is capped at 55.0%.
    - If macro/portfolio stress metrics are missing or unavailable, conviction is capped at 75.0%.
    """
    capped_conviction = float(conviction_pct)
    reasons: List[str] = []

    # Check technical indicators
    tech_unavailable = list(missing_technical_fields or [])
    if tech_snapshot is not None:
        unavail = getattr(tech_snapshot, "fields_unavailable", None)
        if isinstance(unavail, list):
            tech_unavailable.extend(unavail)
        has_macd = getattr(tech_snapshot, "macd_line", None) is not None or getattr(tech_snapshot, "macd", None) is not None
        if not has_macd:
            tech_unavailable.append("macd")
        if getattr(tech_snapshot, "rsi_14", None) is None:
            tech_unavailable.append("rsi_14")
        if getattr(tech_snapshot, "atr_14", None) is None:
            tech_unavailable.append("atr_14")

    missing_core_tech = any(
        k in " ".join(tech_unavailable).lower()
        for k in ["rsi", "macd", "atr"]
    )
    if missing_core_tech:
        if capped_conviction > 55.0:
            capped_conviction = 55.0
            reasons.append("Capped conviction at 55.0% due to missing or degraded technical indicators (RSI-14/MACD/ATR-14).")

    # Check macro / stress metrics
    if stress_metrics is None:
        if capped_conviction > 75.0:
            capped_conviction = 75.0
            reasons.append("Capped conviction at 75.0% due to missing portfolio stress/macro risk metrics.")

    return round(capped_conviction, 1), reasons


def compute_deterministic_position_size(
    portfolio_equity: float,
    portfolio_cash: float,
    current_price: float,
    atr_14: Optional[float] = None,
    conviction_pct: float = 70.0,
    median_adv_shares_30d: float = 0.0,
    risk_budget_pct: float = 0.75,
    max_position_pct: float = 10.0,
) -> Dict[str, Any]:
    """
    Deterministic volatility- and liquidity-adjusted position sizing engine (§2B).
    1. Volatility Stop Distance: 2x ATR-14.
    2. Dollar Risk Budget: portfolio_equity * (risk_budget_pct / 100.0).
    3. Volatility Sized Shares: dollar_risk_budget / (2 * atr_14) if atr_14 > 0, else default 8% stop.
    4. Conviction Scaling: conviction_pct / 100.0 multiplier.
    5. ADV Turnover Cap: Maximum 1.0% of 30-day median dollar turnover (compute_adv_liquidity_constraints).
    6. Portfolio Equity Cap: Max max_position_pct% of total portfolio equity.
    7. Cash Limit: Max available cash.
    """
    current_price = max(0.01, float(current_price))
    portfolio_equity = max(100.0, float(portfolio_equity))
    conviction_factor = max(0.1, min(1.0, float(conviction_pct) / 100.0))
    dollar_risk_budget = portfolio_equity * (risk_budget_pct / 100.0)

    stop_distance = (2.0 * atr_14) if (atr_14 and atr_14 > 0) else (current_price * 0.08)
    vol_shares = dollar_risk_budget / stop_distance
    raw_target_usd = vol_shares * current_price * conviction_factor

    # Max equity allocation cap (default 10% of portfolio)
    equity_cap_usd = portfolio_equity * (max_position_pct / 100.0)
    capped_target_usd = min(raw_target_usd, equity_cap_usd)

    # Cash constraint
    cash_cap_usd = max(0.0, float(portfolio_cash))
    if cash_cap_usd > 0:
        capped_target_usd = min(capped_target_usd, cash_cap_usd)

    # ADV turnover liquidity constraints (1% median ADV 30d turnover)
    adv_constraints = compute_adv_liquidity_constraints(
        position_shares=capped_target_usd / current_price,
        current_price=current_price,
        median_adv_shares_30d=median_adv_shares_30d,
        conviction_sized_usd=capped_target_usd,
    )
    final_target_usd = adv_constraints["recommended_max_usd"]
    final_shares = round(final_target_usd / current_price, 4) if current_price > 0 else 0.0

    return {
        "target_position_usd": round(final_target_usd, 2),
        "target_shares": final_shares,
        "stop_loss_price": round(max(0.01, current_price - stop_distance), 2),
        "stop_distance_usd": round(stop_distance, 2),
        "stop_distance_pct": round((stop_distance / current_price) * 100.0, 2),
        "raw_target_usd": round(raw_target_usd, 2),
        "equity_cap_usd": round(equity_cap_usd, 2),
        "adv_constraints": adv_constraints,
        "sizing_notes": (
            f"Risk budget {risk_budget_pct}% (${round(dollar_risk_budget, 2)}), "
            f"stop distance ${round(stop_distance, 2)} (2x ATR-14), "
            f"conviction {round(conviction_pct, 1)}%."
        ),
    }


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

