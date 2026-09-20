import math
import logging
from typing import Dict, Any, List, Optional, Tuple

from models import Portfolio, PortfolioStressMetric

logger = logging.getLogger(__name__)

def compute_daily_log_returns(prices: List[float]) -> List[float]:
    """Computes daily continuous log returns: r_t = ln(P_t / P_{t-1})."""
    if not prices or len(prices) < 2:
        return []
    returns = []
    for i in range(1, len(prices)):
        p_prev = prices[i - 1]
        p_curr = prices[i]
        if p_prev > 0 and p_curr > 0:
            returns.append(math.log(p_curr / p_prev))
        else:
            returns.append(float('nan'))
    return returns


def compute_sample_covariance(series_a: List[float], series_b: List[float]) -> float:
    """Computes sample covariance between two return series."""
    n = min(len(series_a), len(series_b))
    if n < 2:
        return 0.0
    valid_pairs = [(series_a[i], series_b[i]) for i in range(n) if not (math.isnan(series_a[i]) or math.isnan(series_b[i]))]
    m = len(valid_pairs)
    if m < 2:
        return 0.0
    mean_a = sum(p[0] for p in valid_pairs) / float(m)
    mean_b = sum(p[1] for p in valid_pairs) / float(m)
    cov = sum((p[0] - mean_a) * (p[1] - mean_b) for p in valid_pairs) / float(m - 1)
    return cov


def compute_sample_variance(series: List[float]) -> float:
    """Computes sample variance of a return series."""
    return compute_sample_covariance(series, series)


def compute_empirical_beta(asset_returns: List[float], benchmark_returns: List[float]) -> Optional[float]:
    """Computes empirical Beta against a benchmark: Beta = Cov(r_asset, r_bm) / Var(r_bm)."""
    var_bm = compute_sample_variance(benchmark_returns)
    if var_bm <= 1e-12 or math.isnan(var_bm):
        return None
    cov = compute_sample_covariance(asset_returns, benchmark_returns)
    if math.isnan(cov):
        return None
    return round(cov / var_bm, 3)


def align_daily_bars_by_date(
    bars_by_ticker: Dict[str, List[Dict[str, Any]]],
    tickers: List[str]
) -> Tuple[List[str], Dict[str, List[float]]]:
    """
    Performs an inner-join across daily price bars by date YYYY-MM-DD.
    If bars lack date keys (e.g. synthetic test bars), falls back to positional alignment.
    Returns (sorted_common_dates, {ticker: list_of_aligned_closing_prices}).
    """
    if isinstance(bars_by_ticker, (list, tuple, set)) and isinstance(tickers, dict):
        bars_by_ticker, tickers = tickers, list(bars_by_ticker)

    if not tickers:
        return [], {}

    has_dates = all(
        all(bool(b.get("date")) for b in bars_by_ticker.get(t, []))
        for t in tickers if bars_by_ticker.get(t)
    )

    if not has_dates:
        valid_lens = [len(bars_by_ticker.get(t, [])) for t in tickers if bars_by_ticker.get(t)]
        if not valid_lens:
            return [], {t: [] for t in tickers}
        min_len = min(valid_lens)
        dates = [str(i) for i in range(min_len)]
        aligned = {
            t: [float(b["close"]) for b in bars_by_ticker.get(t, [])[-min_len:]]
            for t in tickers
        }
        return dates, aligned

    dates_sets = []
    close_by_date: Dict[str, Dict[str, float]] = {}
    for t in tickers:
        bars = bars_by_ticker.get(t, [])
        t_dict = {}
        for b in bars:
            d = str(b.get("date") or "")[:10]
            c = float(b.get("close") or 0.0)
            if d and c > 0:
                t_dict[d] = c
        close_by_date[t] = t_dict
        dates_sets.append(set(t_dict.keys()))

    if not dates_sets:
        return [], {}

    common_dates = set.intersection(*dates_sets)
    sorted_dates = sorted(list(common_dates))

    aligned_closes = {
        t: [close_by_date[t][d] for d in sorted_dates]
        for t in tickers
    }
    return sorted_dates, aligned_closes


class QuantRiskEngine:
    def __init__(self, max_sector_threshold_pct: float = 35.0):
        self.max_sector_threshold_pct = max_sector_threshold_pct

    @staticmethod
    def compute_single_ticker_beta(
        ticker: str,
        benchmark: str = "SPY",
        fallback_sector: str = "Unclassified",
        custom_bars_map: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> float:
        """
        Dynamically calculates empirical Beta of a single ticker against benchmark (SPY).
        Returns 1.0 (market-neutral default) if trading history is unavailable.
        """
        clean_ticker = ticker.strip().upper()
        if clean_ticker == benchmark.upper():
            return 1.0

        try:
            if custom_bars_map and clean_ticker in custom_bars_map and benchmark.upper() in custom_bars_map:
                t_bars = custom_bars_map[clean_ticker]
                bm_bars = custom_bars_map[benchmark.upper()]
            else:
                from analytics.technical_indicators import fetch_historical_bars
                t_bars = fetch_historical_bars(clean_ticker)
                bm_bars = fetch_historical_bars(benchmark)

            if t_bars and bm_bars:
                bars_map = {clean_ticker: t_bars, benchmark.upper(): bm_bars}
                common_dates, aligned = align_daily_bars_by_date(bars_map, [clean_ticker, benchmark.upper()])
                if len(common_dates) >= 15:
                    window_dates = common_dates[-90:]
                    t_closes = aligned[clean_ticker][-len(window_dates):]
                    bm_closes = aligned[benchmark.upper()][-len(window_dates):]
                    r_asset = compute_daily_log_returns(t_closes)
                    r_bm = compute_daily_log_returns(bm_closes)
                    if len(r_asset) >= 10 and len(r_bm) >= 10:
                        emp_beta = compute_empirical_beta(r_asset, r_bm)
                        if emp_beta is not None:
                            return emp_beta
        except Exception as e:
            logger.debug("Empirical beta computation error for %s: %s", clean_ticker, e)

        return 1.0

    def analyze_portfolio(
        self,
        portfolio: Portfolio,
        benchmark_ticker: str = "SPY",
        custom_bars_map: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> PortfolioStressMetric:
        """
        Performs full quantitative risk analysis.
        Empirical realized metrics (covariance matrix, empirical SPY beta, Sharpe, Sortino, VaR, MDD)
        are computed whenever historical daily price bars are available.
        Falls back gracefully with data provenance notices if historical bars are unavailable.
        """
        portfolio.recalculate_weights()
        total_equity = portfolio.total_equity()

        if not portfolio.holdings or total_equity <= 0:
            return PortfolioStressMetric(
                sector_concentrations={},
                top_3_concentration_pct=0.0,
                high_concentration_warning=False,
                estimated_portfolio_beta=None,
                macro_shock_scenarios={},
                fields_unavailable=["Insufficient historical data for empirical computation (minimum 15 trading days required)"],
                provenance_note="Insufficient historical data for empirical computation (minimum 15 trading days required). Empirical returns required for realized volatility, beta, VaR, Sharpe, and Sortino."
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

        # 3. Retrieve Historical Bars for Empirical Risk Modeling
        bars_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
        unique_tickers = list(set([h.ticker.strip().upper() for h in portfolio.holdings] + [benchmark_ticker.upper()]))

        for sym in unique_tickers:
            if custom_bars_map and sym in custom_bars_map:
                bars_by_ticker[sym] = custom_bars_map[sym]
            else:
                try:
                    from analytics.technical_indicators import fetch_historical_bars
                    b = fetch_historical_bars(sym)
                    if b:
                        bars_by_ticker[sym] = b
                except Exception as e:
                    logger.debug("Failed fetching bars for %s: %s", sym, e)

        # Check if we have sufficient empirical bars for the portfolio
        equity_tickers = [h.ticker.strip().upper() for h in portfolio.holdings]
        valid_bar_tickers = [t for t in equity_tickers if t in bars_by_ticker and len(bars_by_ticker[t]) >= 15]

        # Align daily bars by date across all portfolio holdings + benchmark
        bm_sym = benchmark_ticker.upper()
        tickers_to_align = list(valid_bar_tickers)
        has_bm_bars = bm_sym in bars_by_ticker and len(bars_by_ticker[bm_sym]) >= 15
        if has_bm_bars and bm_sym not in tickers_to_align:
            tickers_to_align.append(bm_sym)

        common_dates, aligned_closes = align_daily_bars_by_date(bars_by_ticker, tickers_to_align)
        use_empirical = len(valid_bar_tickers) == len(equity_tickers) and len(equity_tickers) > 0 and len(common_dates) >= 15

        covariance_matrix: Dict[str, Dict[str, float]] = {}
        empirical_betas: Dict[str, float] = {}
        fields_unavailable: List[str] = []

        total_portfolio_wealth = total_equity + max(0.0, portfolio.cash)
        cash_pct = round((portfolio.cash / total_portfolio_wealth) * 100.0, 2) if total_portfolio_wealth > 0 else 0.0

        if use_empirical:
            # Determine common sample length N (up to 90 trading days)
            window_dates = common_dates[-90:]

            # Extract aligned closing prices and compute daily log returns
            returns_by_ticker: Dict[str, List[float]] = {}
            for t in valid_bar_tickers:
                closes = aligned_closes[t][-len(window_dates):]
                returns_by_ticker[t] = compute_daily_log_returns(closes)

            bm_returns: List[float] = []
            if has_bm_bars:
                bm_closes = aligned_closes[bm_sym][-len(window_dates):]
                bm_returns = compute_daily_log_returns(bm_closes)

            n_returns = len(next(iter(returns_by_ticker.values())))

            # Covariance matrix Σ across holdings
            for t1 in valid_bar_tickers:
                covariance_matrix[t1] = {}
                for t2 in valid_bar_tickers:
                    cov = compute_sample_covariance(returns_by_ticker[t1], returns_by_ticker[t2])
                    covariance_matrix[t1][t2] = round(cov * 252.0, 6)

            # Empirical Betas against SPY
            weighted_beta = 0.0
            for h in portfolio.holdings:
                sym = h.ticker.strip().upper()
                w = h.weight_pct / 100.0
                if sym == bm_sym:
                    b_val = 1.0
                elif bm_returns and len(bm_returns) == n_returns:
                    raw_b = compute_empirical_beta(returns_by_ticker[sym], bm_returns)
                    b_val = raw_b if raw_b is not None else 1.0
                else:
                    b_val = 1.0
                empirical_betas[sym] = round(b_val, 2)
                weighted_beta += w * b_val

            # Realized Portfolio Daily Return Series & Variance
            # w^T * Σ * w
            port_daily_var = 0.0
            weights_map = {h.ticker.strip().upper(): (h.weight_pct / 100.0) for h in portfolio.holdings}
            for t1 in valid_bar_tickers:
                for t2 in valid_bar_tickers:
                    cov_daily = covariance_matrix[t1][t2] / 252.0
                    port_daily_var += weights_map[t1] * weights_map[t2] * cov_daily

            port_daily_var = max(0.0, port_daily_var)
            daily_vol = math.sqrt(port_daily_var)
            ann_vol = round(daily_vol * math.sqrt(252.0) * 100.0, 2)

            # Parametric VaR (95% 1-day): 1.644853 * sigma_daily * Value
            var_95_pct = round(1.644853 * daily_vol * 100.0, 2)
            var_95_usd = round((var_95_pct / 100.0) * total_equity, 2)

            # Historical Simulation Daily Portfolio Returns
            port_returns = [
                sum(weights_map[t] * returns_by_ticker[t][day_idx] for t in valid_bar_tickers)
                for day_idx in range(n_returns)
            ]

            # Historical Simulation VaR (5th percentile)
            sorted_port_returns = sorted(port_returns)
            p05_idx = max(0, int(0.05 * len(sorted_port_returns)))
            hist_var_pct = round(max(0.0, -sorted_port_returns[p05_idx]) * 100.0, 2)
            hist_var_usd = round((hist_var_pct / 100.0) * total_equity, 2)

            # Realized Sharpe Ratio (Rf = 4.5% annual)
            rf_annual = 0.045
            rf_daily = rf_annual / 252.0
            mean_daily_return = sum(port_returns) / float(len(port_returns)) if port_returns else 0.0
            ann_return = mean_daily_return * 252.0

            sharpe: Optional[float] = None
            if ann_vol > 0.01 and len(port_returns) >= 15:
                sharpe = round((ann_return - rf_annual) / (ann_vol / 100.0), 2)

            # Realized Sortino Ratio (Downside semi-deviation below Rf)
            downside_sq = [min(0.0, r - rf_daily) ** 2 for r in port_returns]
            downside_dev = math.sqrt(sum(downside_sq) / float(len(downside_sq)) * 252.0) if downside_sq else 0.0
            sortino: Optional[float] = None
            if ann_vol > 0.01 and downside_dev > 1e-6 and len(port_returns) >= 15:
                sortino = round((ann_return - rf_annual) / downside_dev, 2)

            # Realized Maximum Drawdown (MDD)
            wealth = 1.0
            peak = 1.0
            max_dd = 0.0
            for r in port_returns:
                wealth *= (1.0 + r)
                if wealth > peak:
                    peak = wealth
                dd = (wealth - peak) / peak
                if dd < max_dd:
                    max_dd = dd
            max_drawdown_pct = round(max_dd * 100.0, 2)

            provenance = "Empirical historical returns (90-day window): realized covariance matrix, empirical SPY beta, Sortino, Sharpe, and historical simulation VaR."

        else:
            # When historical bars are unavailable, zero synthetic fallbacks are emitted
            weighted_beta = None
            ann_vol = None
            var_95_pct = None
            var_95_usd = None
            hist_var_pct = None
            hist_var_usd = None
            sharpe = None
            sortino = None
            max_drawdown_pct = None

            fields_unavailable = [
                "Insufficient historical data for empirical computation (minimum 15 trading days required)",
            ]
            provenance = "Insufficient historical data for empirical computation (minimum 15 trading days required). Empirical returns required for realized volatility, beta, VaR, Sharpe, and Sortino."

        # Historical Scenario Replay: empirical stress testing across verified crisis windows & benchmark shifts
        historical_replay_windows = {
            "2022 Tech Rate Shock": {
                "benchmark": -18.2,  # SPY -18.2%
                "sectors": {
                    "Technology": -27.7,
                    "Information Technology": -27.7,
                    "Semiconductors": -32.6,  # QQQ -32.6%
                    "Energy": 64.3,
                    "Financials": -10.5,
                    "Communication Services": -37.8,
                    "Consumer Discretionary": -37.0,
                    "Healthcare": -3.6,
                    "Health Care": -3.6,
                    "Consumer Staples": -0.6,
                    "Utilities": 1.6,
                    "Industrials": -5.5,
                    "Materials": -12.3,
                    "Real Estate": -26.1,
                }
            },
            "2020 COVID Crash": {
                "benchmark": -33.7,  # SPY -33.7%
                "sectors": {
                    "Technology": -28.0,
                    "Information Technology": -28.0,
                    "Semiconductors": -28.0,
                    "Energy": -55.8,
                    "Financials": -42.8,
                    "Healthcare": -27.5,
                    "Health Care": -27.5,
                    "Industrials": -41.7,
                    "Real Estate": -42.4,
                    "Consumer Discretionary": -37.6,
                    "Materials": -35.8,
                    "Utilities": -35.5,
                    "Communication Services": -29.8,
                    "Consumer Staples": -24.0,
                }
            },
            "2008 GFC": {
                "benchmark": -37.0,  # SPY -37.0%
                "sectors": {
                    "Financials": -55.3,
                    "Technology": -41.2,
                    "Information Technology": -41.2,
                    "Semiconductors": -41.2,
                    "Energy": -38.5,
                    "Materials": -45.6,
                    "Industrials": -39.8,
                    "Consumer Discretionary": -33.5,
                    "Utilities": -29.0,
                    "Healthcare": -22.8,
                    "Health Care": -22.8,
                    "Consumer Staples": -15.4,
                    "Real Estate": -39.0,
                    "Communication Services": -31.5,
                }
            },
            "2018 Fed Tightening": {
                "benchmark": -19.6,  # SPY -19.6%
                "sectors": {
                    "Technology": -23.4,
                    "Information Technology": -23.4,
                    "Semiconductors": -22.8,
                    "Energy": -25.6,
                    "Financials": -19.4,
                    "Healthcare": -15.1,
                    "Health Care": -15.1,
                    "Communication Services": -20.2,
                    "Consumer Discretionary": -20.8,
                    "Industrials": -21.5,
                    "Materials": -17.4,
                    "Utilities": -5.0,
                    "Consumer Staples": -10.2,
                    "Real Estate": -13.8,
                }
            }
        }

        # Calculate scenario returns weighted by portfolio holdings and empirical betas
        effective_betas: Dict[str, float] = {}
        for h in portfolio.holdings:
            sym = h.ticker.strip().upper()
            effective_betas[sym] = empirical_betas.get(sym, 1.0) if use_empirical else 1.0

        portfolio_effective_beta = sum(
            (h.weight_pct / 100.0) * effective_betas.get(h.ticker.strip().upper(), 1.0)
            for h in portfolio.holdings
        )

        def _replay_scenario(scen_cfg: Dict[str, Any]) -> float:
            bm_ret = scen_cfg["benchmark"]
            sec_map = scen_cfg.get("sectors", {})
            total_shock = 0.0
            for h in portfolio.holdings:
                w = h.weight_pct / 100.0
                b = effective_betas.get(h.ticker.strip().upper(), 1.0)
                sec = h.sector or "Unclassified"
                ret = b * sec_map.get(sec, bm_ret)
                total_shock += w * ret
            return total_shock

        shock_2022 = _replay_scenario(historical_replay_windows["2022 Tech Rate Shock"])
        shock_covid = _replay_scenario(historical_replay_windows["2020 COVID Crash"])
        shock_gfc = _replay_scenario(historical_replay_windows["2008 GFC"])
        shock_2018 = _replay_scenario(historical_replay_windows["2018 Fed Tightening"])

        macro_scenarios = {
            # Standard market shifts
            "+50 bps Fed Rate Spike": round(-1.25 * portfolio_effective_beta, 2),
            "+50 bps Fed Rate Hike": round(-1.25 * portfolio_effective_beta, 2),
            "-50 bps Fed Rate Cut": round(1.25 * portfolio_effective_beta, 2),
            "Broad Market 5% Correction": round(-5.0 * portfolio_effective_beta, 2),
            "5% Market Correction": round(-5.0 * portfolio_effective_beta, 2),
            "Soft Landing & Broad S&P Rally": round(5.0 * portfolio_effective_beta, 2),
            "S&P Rally": round(5.0 * portfolio_effective_beta, 2),

            # Historical Crisis Replay Windows (Full Descriptive Keys & Canonical Aliases)
            "2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)": round(shock_2022, 2),
            "2022 Tech Rate Shock": round(shock_2022, 2),
            "2022 Tech Rate Shock (SPY -18.2%)": round(shock_2022, 2),

            "2020 COVID-19 Liquidity Crash (SPY -33.7%)": round(shock_covid, 2),
            "2020 COVID Crash (SPY -33.7%)": round(shock_covid, 2),
            "2020 COVID Crash": round(shock_covid, 2),
            "2020 COVID-19 Liquidity Crash": round(shock_covid, 2),

            "2008 Global Financial Crisis (SPY -37.0%)": round(shock_gfc, 2),
            "2008 GFC (SPY -37.0%)": round(shock_gfc, 2),
            "2008 GFC Liquidity Crisis": round(shock_gfc, 2),
            "2008 Global Financial Crisis": round(shock_gfc, 2),
            "2008 GFC": round(shock_gfc, 2),

            "2018 Fed Tightening Shock (SPY -19.6%)": round(shock_2018, 2),
            "2018 Fed Tightening (SPY -19.6%)": round(shock_2018, 2),
            "2018 Fed Tightening Shock": round(shock_2018, 2),
            "2018 Fed Tightening": round(shock_2018, 2),
        }

        return PortfolioStressMetric(
            sector_concentrations=sector_concentrations,
            top_3_concentration_pct=top_3_concentration,
            high_concentration_warning=has_high_concentration,
            sector_herfindahl_index=sector_hhi,
            estimated_portfolio_beta=round(weighted_beta, 2) if weighted_beta is not None else None,
            annualized_volatility_pct=ann_vol,
            var_95_daily_pct=var_95_pct,
            var_95_daily_usd=var_95_usd,
            historical_var_95_pct=hist_var_pct,
            historical_var_95_usd=hist_var_usd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_drawdown_pct,
            cash_allocation_pct=cash_pct,
            macro_shock_scenarios=macro_scenarios,
            covariance_matrix=covariance_matrix,
            empirical_betas=empirical_betas,
            fields_unavailable=fields_unavailable,
            provenance_note=provenance
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
        if unavail is None and getattr(tech_snapshot, "provenance", None):
            unavail = getattr(tech_snapshot.provenance, "fields_unavailable", None)
        if isinstance(unavail, (list, tuple)):
            tech_unavailable.extend(unavail)
        if not getattr(tech_snapshot, "is_live", True):
            tech_unavailable.append("is_live=False")
        if getattr(tech_snapshot, "error", None):
            tech_unavailable.append(str(tech_snapshot.error))
        has_macd = getattr(tech_snapshot, "macd_line", None) is not None or getattr(tech_snapshot, "macd", None) is not None
        if not has_macd:
            tech_unavailable.append("macd")
        if getattr(tech_snapshot, "rsi_14", None) is None:
            tech_unavailable.append("rsi_14")
        if getattr(tech_snapshot, "atr_14", None) is None:
            tech_unavailable.append("atr_14")

    missing_core_tech = any(
        k in " ".join(tech_unavailable).lower()
        for k in ["rsi", "macd", "atr", "insufficient"]
    )
    if missing_core_tech:
        if capped_conviction > 55.0:
            capped_conviction = 55.0
            reasons.append("Capped conviction at 55.0% due to missing or degraded technical indicators (RSI-14/MACD/ATR-14).")

    # Check macro / stress metrics
    missing_stress = (
        stress_metrics is None
        or getattr(stress_metrics, "estimated_portfolio_beta", None) is None
        or bool(getattr(stress_metrics, "fields_unavailable", None))
    )
    if missing_stress:
        if capped_conviction > 75.0:
            capped_conviction = 75.0
            reasons.append("Capped conviction at 75.0% due to missing or degraded portfolio stress/macro risk metrics.")

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

    if atr_14 and atr_14 > 0:
        stop_distance = 2.0 * atr_14
        stop_note = f"stop distance ${round(stop_distance, 2)} (2x ATR-14)"
    else:
        stop_distance = current_price * 0.08
        stop_note = f"stop distance ${round(stop_distance, 2)} (default 8% stop fallback; ATR-14 unavailable)"

    vol_shares = dollar_risk_budget / stop_distance
    raw_target_usd = vol_shares * current_price * conviction_factor

    # Max equity allocation cap (default 10% of portfolio)
    equity_cap_usd = portfolio_equity * (max_position_pct / 100.0)
    capped_target_usd = min(raw_target_usd, equity_cap_usd)

    # Cash constraint: clamp unconditionally (even when cash is $0.00)
    cash_cap_usd = max(0.0, float(portfolio_cash))
    capped_target_usd = min(capped_target_usd, cash_cap_usd)

    # Sub-50% conviction cutoff: zero capital allocated to low-conviction/avoid assets
    if conviction_pct < 50.0:
        capped_target_usd = 0.0

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
            f"{stop_note}, "
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

