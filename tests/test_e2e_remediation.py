"""
tests/test_e2e_remediation.py — Comprehensive End-to-End Test Suite for Architectural Remediation

4-Tier Test Architecture:
- Tier 1: Feature Coverage (>=5 tests per feature)
- Tier 2: Boundary & Corner Cases (>=5 tests per feature)
- Tier 3: Cross-Feature Combinations (Pairwise interactions)
- Tier 4: Real-World Application Scenarios
"""
import ast
import asyncio
import json
import math
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import freezegun
import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st

from agents.base_agent import BaseAgent
from agents.news_ingestion import evaluate_source_reliability
from analytics.economic_calendar import get_economic_calendar_context, format_economic_calendar_for_prompt
from analytics.quant_risk import QuantRiskEngine
from analytics.technical_indicators import compute_technical_snapshot, TechnicalSnapshot
from config import config
from models import Portfolio, PortfolioHolding, PortfolioStressMetric
from web.app import app, orchestrator, create_session_token

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PST = ZoneInfo("America/Los_Angeles")
EST = ZoneInfo("America/New_York")


# ==============================================================================
# Helper Fixtures & Builders
# ==============================================================================

@pytest.fixture
def test_client():
    """Provides a TestClient authenticated with a genuine Sentinel session token."""
    admin = orchestrator.state_store.get_or_create_default_admin()
    token = create_session_token(admin["id"], admin["username"], role="admin")
    client = TestClient(app, cookies={"sentinel_token": token})
    return client


def make_test_bars(base_price: float, count: int = 30, volatility: float = 0.02) -> List[Dict[str, Any]]:
    """Helper to generate realistic oscillating daily price bars."""
    bars = []
    for i in range(count):
        close_p = round(base_price * (1.0 + volatility * math.sin(i * 0.8)), 4)
        bars.append({
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": round(close_p * 0.999, 4),
            "high": round(close_p * 1.015, 4),
            "low": round(close_p * 0.985, 4),
            "close": close_p,
            "volume": 150000
        })
    return bars


def make_subpenny_bars(base_price: float = 0.001, count: int = 60) -> List[Dict[str, Any]]:
    """Helper to generate sub-penny (<0.005) daily price bars."""
    bars = []
    for i in range(count):
        close_p = max(0.0001, round(base_price + 0.0002 * math.sin(i * 0.5), 6))
        bars.append({
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": round(close_p * 0.99, 6),
            "high": round(close_p * 1.02, 6),
            "low": round(close_p * 0.98, 6),
            "close": close_p,
            "volume": 50000
        })
    return bars


# ==============================================================================
# TIER 1: Feature Coverage (>=5 tests per feature)
# ==============================================================================

class TestTier1FeatureCoverage:
    """
    Tier 1 tests cover the canonical functionality and specifications of all 10
    remediation features (F1 - F10) with >= 5 tests per feature.
    """

    # --------------------------------------------------------------------------
    # F1: Sector Macro Sensitivities Excision
    # --------------------------------------------------------------------------
    def test_t1_f1_sensitivities_attribute_error_on_import(self):
        """F1.1: Direct import of SECTOR_MACRO_SENSITIVITIES from quant_risk raises ImportError."""
        import importlib
        with pytest.raises((ImportError, AttributeError)):
            importlib.import_module("analytics.quant_risk").__getattr__("SECTOR_MACRO_SENSITIVITIES")

    def test_t1_f1_sensitivities_not_in_quant_risk_module(self):
        """F1.2: quant_risk module globals do not contain SECTOR_MACRO_SENSITIVITIES."""
        import analytics.quant_risk as qr
        assert not hasattr(qr, "SECTOR_MACRO_SENSITIVITIES")
        assert "SECTOR_MACRO_SENSITIVITIES" not in getattr(qr, "__all__", [])

    def test_t1_f1_ast_scan_quant_risk(self):
        """F1.3: AST verification of analytics/quant_risk.py contains zero references."""
        file_path = PROJECT_ROOT / "analytics" / "quant_risk.py"
        source = file_path.read_text(encoding="utf-8")
        assert "SECTOR_MACRO_SENSITIVITIES" not in source

    def test_t1_f1_sensitivities_absent_across_production_codebase(self):
        """F1.4: Zero occurrences of SECTOR_MACRO_SENSITIVITIES in any non-test production python file."""
        prod_dirs = ["analytics", "agents", "channels", "storage", "web", "auth"]
        occurrences = []
        for d in prod_dirs:
            p = PROJECT_ROOT / d
            if not p.exists():
                continue
            for py_file in p.rglob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                if "SECTOR_MACRO_SENSITIVITIES" in text:
                    occurrences.append(str(py_file))
        assert len(occurrences) == 0, f"Found SECTOR_MACRO_SENSITIVITIES in production files: {occurrences}"

    def test_t1_f1_quant_engine_runs_without_sector_sensitivities(self):
        """F1.5: QuantRiskEngine.analyze_portfolio executes with zero reliance on sector heuristics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Tech Energy Mix",
            cash=1000.0,
            holdings=[
                PortfolioHolding(ticker="NVDA", name="NVIDIA", shares=10, avg_price=100.0, current_price=120.0, sector="Semiconductors"),
                PortfolioHolding(ticker="XOM", name="ExxonMobil", shares=10, avg_price=100.0, current_price=110.0, sector="Energy")
            ]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        assert isinstance(stress, PortfolioStressMetric)
        assert stress.top_3_concentration_pct > 0.0

    # --------------------------------------------------------------------------
    # F2: Empirical Quant Missing-Data Contract
    # --------------------------------------------------------------------------
    def test_t1_f2_no_bars_all_nine_empirical_metrics_none(self):
        """F2.1: Portfolios without bars return None for all 9 empirical metrics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="No Bars Portfolio",
            cash=5000.0,
            holdings=[PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=150.0, current_price=180.0, sector="Technology")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        nine_metrics = [
            stress.estimated_portfolio_beta,
            stress.annualized_volatility_pct,
            stress.var_95_daily_pct,
            stress.var_95_daily_usd,
            stress.historical_var_95_pct,
            stress.historical_var_95_usd,
            stress.sharpe_ratio,
            stress.sortino_ratio,
            stress.max_drawdown_pct,
        ]
        assert all(m is None for m in nine_metrics), f"Expected all 9 metrics to be None, got: {nine_metrics}"

    def test_t1_f2_under_15_bars_all_nine_metrics_none(self):
        """F2.2: Portfolios with <15 bars (e.g. 10 bars) return None for all 9 empirical metrics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Ten Bars Portfolio",
            cash=1000.0,
            holdings=[PortfolioHolding(ticker="TSLA", name="Tesla", shares=5, avg_price=200.0, current_price=210.0, sector="Consumer Discretionary")]
        )
        bars_10 = make_test_bars(210.0, count=10)
        custom_bars = {"TSLA": bars_10, "SPY": make_test_bars(450.0, count=10)}
        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)

        assert stress.estimated_portfolio_beta is None
        assert stress.annualized_volatility_pct is None
        assert stress.var_95_daily_pct is None
        assert stress.var_95_daily_usd is None
        assert stress.historical_var_95_pct is None
        assert stress.historical_var_95_usd is None
        assert stress.sharpe_ratio is None
        assert stress.sortino_ratio is None
        assert stress.max_drawdown_pct is None

    def test_t1_f2_exact_fields_unavailable_disclosure_string(self):
        """F2.3: Exact disclosure string appears in fields_unavailable when bars are missing."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Contract Disclosure Portfolio",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="AMZN", name="Amazon", shares=10, avg_price=100.0, current_price=150.0, sector="Consumer Discretionary")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        expected_str = "Insufficient historical data for empirical computation (minimum 15 trading days required)"
        assert expected_str in stress.fields_unavailable

    def test_t1_f2_no_legacy_affine_beta_excuse_strings(self):
        """F2.4: Legacy heuristic excuse strings are removed from fields_unavailable and provenance."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Legacy Text Test",
            cash=1000.0,
            holdings=[PortfolioHolding(ticker="MSFT", name="Microsoft", shares=10, avg_price=300.0, current_price=350.0, sector="Technology")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        combined_text = " ".join(stress.fields_unavailable) + " " + stress.provenance_note
        assert "sector heuristic beta fallback" not in combined_text.lower()
        assert "affine beta" not in combined_text.lower()

    def test_t1_f2_sufficient_bars_computes_all_nine_metrics(self):
        """F2.5: When >=15 bars are provided, all 9 empirical metrics evaluate to valid numbers."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Empirical Verified Portfolio",
            cash=2000.0,
            holdings=[
                PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=150.0, current_price=180.0, sector="Technology"),
                PortfolioHolding(ticker="MSFT", name="Microsoft", shares=5, avg_price=300.0, current_price=350.0, sector="Technology")
            ]
        )
        custom_bars = {
            "AAPL": make_test_bars(180.0, count=30, volatility=0.03),
            "MSFT": make_test_bars(350.0, count=30, volatility=0.025),
            "SPY": make_test_bars(450.0, count=30, volatility=0.015)
        }
        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)

        assert isinstance(stress.estimated_portfolio_beta, float)
        assert isinstance(stress.annualized_volatility_pct, float)
        assert isinstance(stress.var_95_daily_pct, float)
        assert isinstance(stress.var_95_daily_usd, float)
        assert isinstance(stress.historical_var_95_pct, float)
        assert isinstance(stress.historical_var_95_usd, float)
        assert isinstance(stress.sharpe_ratio, float)
        assert isinstance(stress.sortino_ratio, float)
        assert isinstance(stress.max_drawdown_pct, float)

    # --------------------------------------------------------------------------
    # F3: Historical Macro Shock Replay Engine
    # --------------------------------------------------------------------------
    def test_t1_f3_crisis_windows_keys_and_values(self):
        """F3.1: All 4 canonical crisis windows are present in macro_shock_scenarios."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Crisis Replay Portfolio",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="SPY", name="SPDR S&P 500 ETF", shares=10, avg_price=400.0, current_price=450.0, sector="Broad ETF")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        scenarios = stress.macro_shock_scenarios

        assert any("2022 Tech Rate Shock" in k for k in scenarios)
        assert any("2020 COVID Crash" in k for k in scenarios)
        assert any("2008 GFC" in k for k in scenarios)
        assert any("2018 Fed Tightening" in k for k in scenarios)

    def test_t1_f3_benchmark_spy_drawdowns_match_canonical_crisis(self):
        """F3.2: 100% SPY holding returns exact canonical drawdown percentages for crisis periods."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Pure SPY Holding",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="SPY", name="SPDR S&P 500 ETF", shares=100, avg_price=400.0, current_price=450.0, sector="Broad ETF")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        scenarios = stress.macro_shock_scenarios

        assert scenarios["2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)"] == -18.2
        assert scenarios["2020 COVID Crash (SPY -33.7%)"] == -33.7
        assert scenarios["2008 GFC (SPY -37.0%)"] == -37.0
        assert scenarios["2018 Fed Tightening (SPY -19.6%)"] == -19.6

    def test_t1_f3_macro_shifts_present_and_scaled(self):
        """F3.3: 4 market shifts scale with portfolio effective beta."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Shift Test Portfolio",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="SPY", name="SPY", shares=10, avg_price=400.0, current_price=450.0, sector="Broad ETF")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        scenarios = stress.macro_shock_scenarios

        assert scenarios["+50 bps Fed Rate Spike"] == -1.25
        assert scenarios["-50 bps Fed Rate Cut"] == 1.25
        assert scenarios["Broad Market 5% Correction"] == -5.0
        assert scenarios["Soft Landing & Broad S&P Rally"] == 5.0

    def test_t1_f3_sector_differentiation_in_2022_rate_shock(self):
        """F3.4: In 2022 Rate Shock, Energy is positive (+64.3%) and Technology is negative (-27.7%)."""
        engine = QuantRiskEngine()
        energy_port = Portfolio(
            name="Energy Only",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="XOM", name="Exxon", shares=10, avg_price=100.0, current_price=100.0, sector="Energy")]
        )
        tech_port = Portfolio(
            name="Tech Only",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=100.0, current_price=100.0, sector="Technology")]
        )
        stress_energy = engine.analyze_portfolio(energy_port, custom_bars_map={})
        stress_tech = engine.analyze_portfolio(tech_port, custom_bars_map={})

        energy_shock = stress_energy.macro_shock_scenarios["2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)"]
        tech_shock = stress_tech.macro_shock_scenarios["2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)"]

        assert energy_shock == 64.3
        assert tech_shock == -27.7

    def test_t1_f3_canonical_scenario_aliases(self):
        """F3.5: Canonical aliases exist for backward compatibility."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Alias Test",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="SPY", name="SPY", shares=10, avg_price=400.0, current_price=450.0, sector="Broad ETF")]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        assert "2022 Tech Rate Shock" in stress.macro_shock_scenarios
        assert "2008 GFC Liquidity Crisis" in stress.macro_shock_scenarios

    # --------------------------------------------------------------------------
    # F4: Technical Indicators Sub-Penny & Zero Division Resilience
    # --------------------------------------------------------------------------
    def test_t1_f4_dma_subpenny_distance_guard(self):
        """F4.1: DMA distance calculations with sub-penny prices (<0.005) compute without ZeroDivisionError."""
        subpenny_bars = make_subpenny_bars(base_price=0.001, count=60)
        snap = compute_technical_snapshot("SUBPENNY", custom_bars=subpenny_bars)
        assert snap.sma_50 is not None
        assert snap.dist_from_50_dma_pct is not None
        assert isinstance(snap.dist_from_50_dma_pct, float)

    def test_t1_f4_zero_dma_guard(self):
        """F4.2: Zero SMA value (<= 1e-9) defaults dist_from_dma to 0.0 without crash."""
        flat_zero_bars = [{
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "volume": 0
        } for i in range(55)]
        snap = compute_technical_snapshot("ZERO_DMA", custom_bars=flat_zero_bars)
        assert snap.dist_from_50_dma_pct == 0.0

    def test_t1_f4_rsi_flat_series_resilience(self):
        """F4.3: Flat price series (zero gain/loss deltas) returns neutral RSI 50.0."""
        flat_bars = [{
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000
        } for i in range(25)]
        snap = compute_technical_snapshot("FLAT", custom_bars=flat_bars)
        assert snap.rsi_14 == 50.0
        assert snap.rsi_status == "NEUTRAL"

    def test_t1_f4_bollinger_flat_series_resilience(self):
        """F4.4: Flat price series with zero variance collapses Bollinger bands safely."""
        flat_bars = [{
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 5.0,
            "high": 5.0,
            "low": 5.0,
            "close": 5.0,
            "volume": 1000
        } for i in range(25)]
        snap = compute_technical_snapshot("FLAT_BB", custom_bars=flat_bars)
        assert snap.bollinger_middle == 5.0
        assert snap.bollinger_pct_b == 0.5
        assert snap.bollinger_bandwidth == 0.0

    def test_t1_f4_zero_price_bars_safety(self):
        """F4.5: Series containing intermittent 0.0 price does not crash compute_technical_snapshot."""
        bars = make_test_bars(50.0, count=30)
        bars[10]["close"] = 0.0
        snap = compute_technical_snapshot("ZERO_INTERMITTENT", custom_bars=bars)
        assert isinstance(snap, TechnicalSnapshot)

    # --------------------------------------------------------------------------
    # F5: Dynamic Sources Registry Ingestion & Fallback
    # --------------------------------------------------------------------------
    def test_t1_f5_statutory_authorities_score(self):
        """F5.1: Statutory authority sources evaluate to score 0.99."""
        assert evaluate_source_reliability("https://www.sec.gov/news/press-release") == 0.99
        assert evaluate_source_reliability("Federal Reserve Board") == 0.99

    def test_t1_f5_premier_wires_score(self):
        """F5.2: Premier wire services evaluate to score 0.92."""
        assert evaluate_source_reliability("Reuters Business News") == 0.92
        assert evaluate_source_reliability("https://bloomberg.com/markets") == 0.92
        assert evaluate_source_reliability("Wall Street Journal") == 0.92

    def test_t1_f5_mainstream_media_score(self):
        """F5.3: Mainstream financial media evaluate to score 0.85."""
        assert evaluate_source_reliability("https://www.cnbc.com/2026/rates") == 0.85
        assert evaluate_source_reliability("MarketWatch") == 0.85

    def test_t1_f5_crowdsourced_opinion_score(self):
        """F5.4: Crowdsourced opinion sources evaluate to score 0.65."""
        assert evaluate_source_reliability("SeekingAlpha Bull Thesis") == 0.65
        assert evaluate_source_reliability("The Motley Fool") == 0.65

    def test_t1_f5_unrecognized_source_fallback_075(self):
        """F5.5: Unrecognized source falls back to registry default score 0.75."""
        assert evaluate_source_reliability("https://unknown-substack-analyst.substack.com") == 0.75

    def test_t1_f5_unreadable_registry_fallback_with_warning(self, monkeypatch, caplog):
        """F5.6: Empty/unreadable registry logs warning and returns exact fallback 0.75."""
        import logging
        from storage import state_store
        monkeypatch.setattr(state_store, "get_sources_registry", lambda: {})

        with caplog.at_level(logging.WARNING):
            score = evaluate_source_reliability("Reuters")
            assert score == 0.75
            assert any("Sources registry unavailable" in rec.message for rec in caplog.records)

    # --------------------------------------------------------------------------
    # F6: Elimination of Silent Error Suppression (Bare Excepts)
    # --------------------------------------------------------------------------
    def test_t1_f6_zero_bare_excepts_in_production_code(self):
        """F6.1: Zero bare 'except:' statements exist across non-test production python files."""
        prod_dirs = ["analytics", "agents", "channels", "storage", "web", "auth"]
        bare_excepts = []
        for d in prod_dirs:
            p = PROJECT_ROOT / d
            if not p.exists():
                continue
            for py_file in p.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler) and node.type is None:
                        bare_excepts.append(f"{py_file.name}:{node.lineno}")
        assert len(bare_excepts) == 0, f"Found bare except blocks in production code: {bare_excepts}"

    def test_t1_f6_zero_bare_except_exception_in_production_code(self):
        """F6.2: Zero bare 'except Exception:' statements without error logging exist across production files."""
        prod_dirs = ["analytics", "agents", "channels", "storage", "web", "auth"]
        remaining = []
        for d in prod_dirs:
            p = PROJECT_ROOT / d
            if not p.exists():
                continue
            for py_file in p.rglob("*.py"):
                for line_idx, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith("except Exception:"):
                        remaining.append(f"{py_file.name}:{line_idx}")
        assert len(remaining) == 0, f"Remaining unmigrated 'except Exception:' blocks: {remaining}"

    def test_t1_f6_auth_crypto_typed_exceptions(self):
        """F6.3: auth/crypto.py handles specific typed cryptography exceptions."""
        from auth.crypto import verify_session_token
        assert verify_session_token("invalid_garbage_token") is None

    # --------------------------------------------------------------------------
    # F7: Async File Upload Event Loop Hygiene
    # --------------------------------------------------------------------------
    def test_t1_f7_json_upload_invokes_to_thread(self, test_client):
        """F7.1: Uploading JSON portfolio executes via asyncio.to_thread worker."""
        json_payload = {
            "name": "Async JSON Test",
            "cash": 5000.0,
            "holdings": [
                {"ticker": "NVDA", "name": "NVIDIA", "shares": 10, "avg_price": 100.0, "current_price": 120.0, "sector": "Semiconductors"}
            ]
        }
        file_bytes = json.dumps(json_payload).encode("utf-8")

        with patch("asyncio.to_thread", side_effect=asyncio.to_thread) as mock_thread:
            resp = test_client.post(
                "/api/portfolio/upload",
                files={"file": ("portfolio.json", file_bytes, "application/json")}
            )
            assert resp.status_code == 200
            assert mock_thread.called, "Expected asyncio.to_thread to be invoked during upload"

    def test_t1_f7_csv_upload_invokes_to_thread(self, test_client):
        """F7.2: Uploading CSV portfolio executes via asyncio.to_thread worker."""
        csv_content = (
            "ticker,name,shares,avg_price,current_price,sector\n"
            "AAPL,Apple,10,150.0,180.0,Technology\n"
            "MSFT,Microsoft,5,300.0,350.0,Technology\n"
        ).encode("utf-8")

        with patch("asyncio.to_thread", side_effect=asyncio.to_thread) as mock_thread:
            resp = test_client.post(
                "/api/portfolio/upload",
                files={"file": ("portfolio.csv", csv_content, "text/csv")}
            )
            assert resp.status_code == 200
            assert mock_thread.called, "Expected asyncio.to_thread to be invoked during CSV upload"

    def test_t1_f7_valid_json_upload_status_success(self, test_client):
        """F7.3: Valid JSON upload returns 200 with success status and parsed holdings."""
        data = {
            "name": "Valid JSON Import",
            "cash": 1000.0,
            "holdings": [{"ticker": "GOOGL", "name": "Alphabet", "shares": 5, "avg_price": 120.0, "current_price": 150.0, "sector": "Technology"}]
        }
        resp = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("test.json", json.dumps(data).encode("utf-8"), "application/json")}
        )
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["status"] == "success"
        assert "1 holdings" in res_json["message"]

    def test_t1_f7_valid_csv_upload_status_success(self, test_client):
        """F7.4: Valid CSV upload returns 200 with success status and parsed holdings."""
        csv_text = "ticker,shares,price,sector\nAMZN,20,180.0,Consumer Discretionary\n"
        resp = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("test.csv", csv_text.encode("utf-8"), "text/csv")}
        )
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["status"] == "success"

    def test_t1_f7_malformed_upload_returns_400(self, test_client):
        """F7.5: Malformed JSON or CSV returns HTTP 400 Bad Request without unhandled crash."""
        resp = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("corrupt.json", b"{not_valid_json", "application/json")}
        )
        assert resp.status_code == 400
        assert "Failed to parse JSON" in resp.json()["detail"]

    # --------------------------------------------------------------------------
    # F8: Temporal Determinism via Freezegun
    # --------------------------------------------------------------------------
    @freezegun.freeze_time("2026-09-16 18:00:00-04:00")  # 18:00 EDT = Post-market FOMC
    def test_t1_f8_freezegun_postmarket_fomc_resolved_completed(self):
        """F8.1: Under frozen post-market time (18:00 EDT), FOMC rate decision is COMPLETED."""
        ctx = get_economic_calendar_context(as_of=None)
        assert ctx["today_date"] == "2026-09-16"
        assert ctx["has_completed_fomc_today"] is True
        today_events = ctx["today_events"]
        fomc = next(e for e in today_events if "Decision" in e["name"])
        assert fomc["status"] == "COMPLETED"

    @freezegun.freeze_time("2026-09-16 09:30:00-04:00")  # 09:30 EDT = Pre-market FOMC
    def test_t1_f8_freezegun_premarket_fomc_resolved_pending(self):
        """F8.2: Under frozen pre-market time (09:30 EDT), FOMC rate decision is PENDING."""
        ctx = get_economic_calendar_context(as_of=None)
        assert ctx["today_date"] == "2026-09-16"
        assert ctx["has_completed_fomc_today"] is False
        today_events = ctx["today_events"]
        fomc = next(e for e in today_events if "Decision" in e["name"])
        assert fomc["status"] == "PENDING"

    @freezegun.freeze_time("2026-09-16 18:00:00-04:00")
    def test_t1_f8_freezegun_temporal_briefing_prompt_formatting(self):
        """F8.3: format_economic_calendar_for_prompt produces deterministic text under frozen time."""
        ctx = get_economic_calendar_context(as_of=None)
        prompt = format_economic_calendar_for_prompt(ctx)
        assert "[COMPLETED] Federal Reserve FOMC Interest Rate Decision" in prompt
        assert "TOMORROW'S SCHEDULED CATALYSTS (2026-09-17): None scheduled." in prompt

    @freezegun.freeze_time("2026-10-28 09:00:00-04:00")  # 09:00 EDT Oct 28 2026
    def test_t1_f8_freezegun_october_fomc_pending(self):
        """F8.4: Frozen on October 28 2026 pre-decision, FOMC decision is pending."""
        ctx = get_economic_calendar_context(as_of=None)
        assert ctx["today_date"] == "2026-10-28"
        assert ctx["has_completed_fomc_today"] is False

    # --------------------------------------------------------------------------
    # F9: Pipeline HTTP Transport Mocking
    # --------------------------------------------------------------------------
    def test_t1_f9_query_llm_json_uses_http_transport(self, monkeypatch):
        """F9.1: query_llm_json dispatches POST request through httpx.Client.post."""
        agent = BaseAgent(name="TestAgent", role_description="Test agent role")
        agent.api_key = "test_gemini_key_xyz"

        captured_request = {}

        def mock_post(client_self, url, *args, **kwargs):
            captured_request["url"] = url
            captured_request["json"] = kwargs.get("json")
            captured_request["headers"] = kwargs.get("headers")
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{
                    "content": {
                        "parts": [{"text": json.dumps({"result": "success", "score": 95})}]
                    }
                }]
            }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        res = agent.query_llm_json(prompt="Analyze this asset", system_instruction="Be concise")
        assert res == {"result": "success", "score": 95}
        assert "generativelanguage.googleapis.com" in captured_request["url"]
        assert captured_request["headers"]["x-goog-api-key"] == "test_gemini_key_xyz"
        assert captured_request["json"]["contents"][0]["parts"][0]["text"] == "Analyze this asset"
        assert "systemInstruction" in captured_request["json"]

    def test_t1_f9_candidate_model_failover_on_400(self, monkeypatch):
        """F9.2: When primary candidate model returns HTTP 400, second candidate model is tried."""
        agent = BaseAgent(name="FailoverAgent", role_description="Test failover role")
        agent.api_key = "test_key"
        attempted_urls = []

        def mock_post(client_self, url, *args, **kwargs):
            attempted_urls.append(url)
            mock_resp = MagicMock(spec=httpx.Response)
            if len(attempted_urls) == 1:
                mock_resp.status_code = 400
                mock_resp.json.return_value = {"error": "Invalid argument"}
            else:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "candidates": [{
                        "content": {"parts": [{"text": json.dumps({"recovered": True})}]}
                    }]
                }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)
        res = agent.query_llm_json(prompt="Test prompt")
        assert res == {"recovered": True}
        assert len(attempted_urls) >= 2

    def test_t1_f9_thinking_config_stripping_on_rejection(self, monkeypatch):
        """F9.3: ThinkingConfig is stripped and retried if model rejects it with 400."""
        agent = BaseAgent(name="ThinkingAgent", role_description="Test thinking role")
        agent.api_key = "test_key"
        payloads_sent = []

        def mock_post(client_self, url, *args, **kwargs):
            payload = kwargs.get("json", {})
            payloads_sent.append(payload)
            mock_resp = MagicMock(spec=httpx.Response)
            if "thinkingConfig" in payload.get("generationConfig", {}):
                mock_resp.status_code = 400
                mock_resp.json.return_value = {"error": "thinkingConfig unsupported"}
            else:
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "candidates": [{"content": {"parts": [{"text": json.dumps({"ok": 1})}]}}]
                }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)
        res = agent.query_llm_json(prompt="Test prompt", thinking_budget=1024)
        assert res == {"ok": 1}
        assert any("thinkingConfig" not in p.get("generationConfig", {}) for p in payloads_sent)

    def test_t1_f9_circuit_breaker_skips_degraded_model(self, monkeypatch):
        """F9.4: Degraded model marked in circuit breaker is deprioritized."""
        from agents.base_agent import MODEL_CIRCUIT_BREAKER
        agent = BaseAgent(name="CircuitAgent", role_description="Test circuit breaker role")
        agent.api_key = "test_key"
        MODEL_CIRCUIT_BREAKER["gemini-3.8-flash"] = 9999999999.0  # Far in future

        called_models = []

        def mock_post(client_self, url, *args, **kwargs):
            model_name = url.split("models/")[1].split(":")[0]
            called_models.append(model_name)
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": json.dumps({"model": model_name})}]}}]
            }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)
        res = agent.query_llm_json(prompt="Test")
        assert called_models[0] != "gemini-3.8-flash"
        MODEL_CIRCUIT_BREAKER.clear()

    # --------------------------------------------------------------------------
    # F10: Hypothesis Property Bounds Expansion
    # --------------------------------------------------------------------------
    @settings(max_examples=30, deadline=None)
    @given(price=st.floats(min_value=0.0001, max_value=5000.0, allow_nan=False, allow_infinity=False))
    def test_t1_f10_rsi_bounded_down_to_0001(self, price):
        """F10.1: Property: RSI-14 remains bounded in [0.0, 100.0] for prices down to 0.0001."""
        bars = []
        for i in range(25):
            p = max(0.0001, round(price * (1.0 + 0.02 * math.sin(i)), 6))
            bars.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000})
        snap = compute_technical_snapshot("PROP_TEST", custom_bars=bars)
        assert snap.rsi_14 is not None
        assert 0.0 <= snap.rsi_14 <= 100.0

    @settings(max_examples=30, deadline=None)
    @given(price=st.floats(min_value=0.0001, max_value=5000.0, allow_nan=False, allow_infinity=False))
    def test_t1_f10_bollinger_bands_ordered_down_to_0001(self, price):
        """F10.2: Property: Bollinger bands satisfy lower <= middle <= upper down to 0.0001."""
        bars = []
        for i in range(25):
            p = max(0.0001, round(price * (1.0 + 0.015 * math.cos(i)), 6))
            bars.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000})
        snap = compute_technical_snapshot("PROP_BB", custom_bars=bars)
        assert snap.bollinger_lower is not None
        assert snap.bollinger_middle is not None
        assert snap.bollinger_upper is not None
        assert snap.bollinger_lower <= snap.bollinger_middle <= snap.bollinger_upper

    @settings(max_examples=30, deadline=None)
    @given(price=st.floats(min_value=0.0001, max_value=5000.0, allow_nan=False, allow_infinity=False))
    def test_t1_f10_atr_non_negative_down_to_0001(self, price):
        """F10.3: Property: ATR-14 remains >= 0.0 for prices down to 0.0001."""
        bars = []
        for i in range(25):
            p = max(0.0001, round(price * (1.0 + 0.02 * math.sin(i)), 6))
            bars.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": p, "high": p * 1.05, "low": p * 0.95, "close": p, "volume": 1000})
        snap = compute_technical_snapshot("PROP_ATR", custom_bars=bars)
        assert snap.atr_14 is not None
        assert snap.atr_14 >= 0.0

    @settings(max_examples=30, deadline=None)
    @given(price=st.floats(min_value=0.0001, max_value=5000.0, allow_nan=False, allow_infinity=False))
    def test_t1_f10_dma_division_resilience_down_to_0001(self, price):
        """F10.4: Property: DMA distance does not raise ZeroDivisionError for prices down to 0.0001."""
        bars = []
        for i in range(60):
            p = max(0.0001, round(price * (1.0 + 0.01 * math.sin(i)), 6))
            bars.append({"date": f"2026-01-{(i % 28) + 1:02d}", "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000})
        snap = compute_technical_snapshot("PROP_DMA", custom_bars=bars)
        assert snap.dist_from_50_dma_pct is not None
        assert isinstance(snap.dist_from_50_dma_pct, float)


# ==============================================================================
# TIER 2: Boundary & Corner Cases (>=5 tests per feature)
# ==============================================================================

class TestTier2BoundaryAndCornerCases:
    """
    Tier 2 tests exercise boundary conditions, extreme floats, empty inputs,
    corrupted data, and edge conditions across all remediation areas.
    """

    def test_t2_zero_portfolio_equity(self):
        """T2.1: Portfolio with 0 cash and 0 holdings returns None for empirical metrics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(name="Zero Portfolio", cash=0.0, holdings=[])
        stress = engine.analyze_portfolio(portfolio)
        assert stress.top_3_concentration_pct == 0.0
        assert stress.estimated_portfolio_beta is None
        assert stress.macro_shock_scenarios == {}

    def test_t2_single_bar_holding_contract(self):
        """T2.2: Holding with exactly 1 historical bar returns None for all 9 empirical metrics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Single Bar",
            cash=100.0,
            holdings=[PortfolioHolding(ticker="XYZ", name="Single Bar Stock", shares=1, avg_price=10.0, current_price=10.0, sector="Technology")]
        )
        custom_bars = {
            "XYZ": [{"date": "2026-01-01", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 100}],
            "SPY": make_test_bars(450.0, count=25)
        }
        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)
        assert stress.estimated_portfolio_beta is None
        assert stress.sharpe_ratio is None
        assert stress.var_95_daily_pct is None

    def test_t2_extreme_subpenny_boundary_00001(self):
        """T2.3: Extreme boundary sub-penny price at 0.0001 computes safely."""
        bars = [{
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 0.0001,
            "high": 0.0001,
            "low": 0.0001,
            "close": 0.0001,
            "volume": 1000
        } for i in range(30)]
        snap = compute_technical_snapshot("MICRO_PENNY", custom_bars=bars)
        assert snap.current_price == 0.0
        assert snap.rsi_14 == 50.0

    def test_t2_all_zero_price_series_contract(self):
        """T2.4: Bankrupt or halted equity with series of all 0.0 prices does not crash."""
        bars = [{
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "volume": 0
        } for i in range(30)]
        snap = compute_technical_snapshot("HALTED", custom_bars=bars)
        assert snap.current_price == 0.0
        assert snap.rsi_status in ("NEUTRAL", "INSUFFICIENT_DATA")

    def test_t2_asymmetric_holding_bars(self):
        """T2.5: Asymmetric bars: 1 ticker has 100 bars, other has only 5 bars -> returns None for empirical metrics."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Asymmetric Bars Portfolio",
            cash=1000.0,
            holdings=[
                PortfolioHolding(ticker="AAPL", name="Apple", shares=10, avg_price=150.0, current_price=180.0, sector="Technology"),
                PortfolioHolding(ticker="NEWCO", name="New Co", shares=10, avg_price=10.0, current_price=12.0, sector="Technology")
            ]
        )
        custom_bars = {
            "AAPL": make_test_bars(180.0, count=100),
            "NEWCO": make_test_bars(12.0, count=5),  # Fails >= 15 threshold
            "SPY": make_test_bars(450.0, count=100)
        }
        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)
        assert stress.estimated_portfolio_beta is None
        assert stress.annualized_volatility_pct is None
        assert stress.var_95_daily_pct is None
        assert "Insufficient historical data for empirical computation (minimum 15 trading days required)" in stress.fields_unavailable

    def test_t2_off_by_one_bars_boundary(self):
        """T2.6: Off-by-one boundary: exactly 14 bars fails empirical contract, 15 bars passes."""
        engine = QuantRiskEngine()
        port14 = Portfolio(
            name="Fourteen Bars",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="T14", name="T14", shares=10, avg_price=100.0, current_price=100.0, sector="Technology")]
        )
        port15 = Portfolio(
            name="Fifteen Bars",
            cash=0.0,
            holdings=[PortfolioHolding(ticker="T15", name="T15", shares=10, avg_price=100.0, current_price=100.0, sector="Technology")]
        )
        bars14 = {"T14": make_test_bars(100.0, count=14), "SPY": make_test_bars(450.0, count=14)}
        bars15 = {"T15": make_test_bars(100.0, count=15), "SPY": make_test_bars(450.0, count=15)}

        stress14 = engine.analyze_portfolio(port14, custom_bars_map=bars14)
        stress15 = engine.analyze_portfolio(port15, custom_bars_map=bars15)

        assert stress14.estimated_portfolio_beta is None
        assert stress15.estimated_portfolio_beta is not None

    def test_t2_empty_and_corrupt_sources_registry(self, monkeypatch):
        """T2.7: Corrupt sources registry JSON returns fallback score 0.75."""
        from storage import state_store
        monkeypatch.setattr(state_store, "get_sources_registry", lambda: None)
        assert evaluate_source_reliability("bloomberg.com") == 0.75

    def test_t2_async_upload_empty_files(self, test_client):
        """T2.8: Empty CSV (0 bytes) and empty JSON ({}) upload returns HTTP 400."""
        resp_csv = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("empty.csv", b"", "text/csv")}
        )
        assert resp_csv.status_code == 400

        resp_json = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("empty.json", b"{}", "application/json")}
        )
        assert resp_json.status_code == 400

    @freezegun.freeze_time("2026-12-31 23:59:59-05:00")
    def test_t2_freezegun_year_boundary_roll(self):
        """T2.9: Pinned at New Year's Eve 2026, context maintains correct year and horizon dates."""
        ctx = get_economic_calendar_context(as_of=None)
        assert ctx["today_date"] == "2026-12-31"

    def test_t2_http_transport_malformed_json_response(self, monkeypatch):
        """T2.10: LLM returns 200 with invalid unparseable text handled gracefully."""
        agent = BaseAgent(name="MalformedAgent", role_description="Test malformed role")
        agent.api_key = "test_key"

        def mock_post(client_self, url, *args, **kwargs):
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "THIS IS NOT JSON AT ALL"}]}}]
            }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)
        res = agent.query_llm_json(prompt="Hello")
        assert res is None


# ==============================================================================
# TIER 3: Cross-Feature Combinations (Pairwise Interactions)
# ==============================================================================

class TestTier3CrossFeatureCombinations:
    """
    Tier 3 tests validate cross-feature interactions across quant risk, technical
    indicators, async API ingestion, temporal pinning, and HTTP transports.
    """

    def test_t3_quant_risk_with_subpenny_technical_bars(self):
        """T3.1: Sub-penny price bars fed into both technical snapshot and quant risk engine."""
        subpenny_bars = make_subpenny_bars(base_price=0.002, count=30)
        snap = compute_technical_snapshot("SUBPENNY_COMBINED", custom_bars=subpenny_bars)
        assert snap.rsi_14 is not None

        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Subpenny Risk Port",
            cash=100.0,
            holdings=[PortfolioHolding(ticker="SUB", name="Subpenny Asset", shares=10000, avg_price=0.002, current_price=0.002, sector="Technology")]
        )
        custom_bars = {"SUB": subpenny_bars, "SPY": make_test_bars(450.0, count=30)}
        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)
        assert stress.estimated_portfolio_beta is not None
        assert stress.annualized_volatility_pct is not None

    def test_t3_async_upload_with_missing_bars_empirical_contract(self, test_client):
        """T3.2: Uploading portfolio without bars returns None for all 9 metrics in API response."""
        data = {
            "name": "Async Missing Data Port",
            "cash": 1000.0,
            "holdings": [{"ticker": "UNLISTED_TICKER_XYZ", "name": "Unlisted", "shares": 10, "avg_price": 50.0, "current_price": 50.0, "sector": "Healthcare"}]
        }
        resp = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("unlisted.json", json.dumps(data).encode("utf-8"), "application/json")}
        )
        assert resp.status_code == 200
        stress = resp.json()["stress"]
        assert stress["estimated_portfolio_beta"] is None
        assert stress["annualized_volatility_pct"] is None
        assert stress["var_95_daily_pct"] is None
        assert "Insufficient historical data for empirical computation (minimum 15 trading days required)" in stress["fields_unavailable"]

    def test_t3_crisis_replay_with_energy_tech_barbell(self):
        """T3.3: 50/50 Energy/Tech barbell portfolio replaying 2022 Rate Shock."""
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Barbell Port",
            cash=0.0,
            holdings=[
                PortfolioHolding(ticker="XOM", name="Exxon", shares=50, avg_price=100.0, current_price=100.0, sector="Energy"),
                PortfolioHolding(ticker="AAPL", name="Apple", shares=50, avg_price=100.0, current_price=100.0, sector="Technology")
            ]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})
        shock_2022 = stress.macro_shock_scenarios["2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)"]
        # Expected: 0.5 * 64.3 + 0.5 * (-27.7) = 32.15 - 13.85 = +18.3%
        assert shock_2022 == 18.3

    @freezegun.freeze_time("2026-09-16 18:00:00-04:00")
    def test_t3_sources_registry_with_freezegun_temporal_news(self):
        """T3.4: Dynamic sources evaluation coupled with frozen temporal timestamp."""
        score_sec = evaluate_source_reliability("sec.gov/press-release")
        score_reuters = evaluate_source_reliability("reuters.com/business")
        assert score_sec == 0.99
        assert score_reuters == 0.92

        ctx = get_economic_calendar_context(as_of=None)
        assert ctx["today_date"] == "2026-09-16"
        assert ctx["has_completed_fomc_today"] is True

    def test_t3_http_transport_mocking_with_token_governance(self, monkeypatch):
        """T3.5: HTTP transport mocking when token governance budget is exhausted returns None."""
        agent = BaseAgent(name="BudgetAgent", role_description="Test budget role")
        agent.api_key = "test_key"

        with patch.object(agent.token_manager, "check_budget_status") as mock_budget:
            mock_status = MagicMock()
            mock_status.budget_exhausted = True
            mock_budget.return_value = mock_status

            with patch.object(config, "enable_token_governance", True):
                res = agent.query_llm_json(prompt="Test prompt")
                assert res is None


# ==============================================================================
# TIER 4: Real-World Application Scenarios
# ==============================================================================

class TestTier4RealWorldApplicationScenarios:
    """
    Tier 4 tests execute multi-component, end-to-end workflows representing
    production operations, institutional risk audits, and live crisis replays.
    """

    def test_t4_full_5_asset_crisis_replay_scenario(self):
        """
        T4.1: Real-world 5-asset institutional portfolio (Tech, Healthcare, Energy, Financials, Consumer)
        evaluated under all 4 historical crisis periods with empirical covariance and VaR.
        """
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Institutional Multi-Sector Fund",
            cash=10000.0,
            holdings=[
                PortfolioHolding(ticker="AAPL", name="Apple Inc", shares=100, avg_price=170.0, current_price=180.0, sector="Technology"),
                PortfolioHolding(ticker="JNJ", name="Johnson & Johnson", shares=80, avg_price=150.0, current_price=160.0, sector="Healthcare"),
                PortfolioHolding(ticker="XOM", name="Exxon Mobil", shares=100, avg_price=100.0, current_price=110.0, sector="Energy"),
                PortfolioHolding(ticker="JPM", name="JPMorgan Chase", shares=60, avg_price=180.0, current_price=200.0, sector="Financials"),
                PortfolioHolding(ticker="AMZN", name="Amazon.com", shares=70, avg_price=170.0, current_price=190.0, sector="Consumer Discretionary"),
            ]
        )

        custom_bars = {
            "AAPL": make_test_bars(180.0, count=40, volatility=0.03),
            "JNJ": make_test_bars(160.0, count=40, volatility=0.015),
            "XOM": make_test_bars(110.0, count=40, volatility=0.025),
            "JPM": make_test_bars(200.0, count=40, volatility=0.022),
            "AMZN": make_test_bars(190.0, count=40, volatility=0.035),
            "SPY": make_test_bars(450.0, count=40, volatility=0.018),
        }

        stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)

        # Mathematical integrity checks
        assert stress.estimated_portfolio_beta is not None
        assert stress.annualized_volatility_pct > 0.0
        assert stress.var_95_daily_pct > 0.0
        assert stress.var_95_daily_usd > 0.0
        assert stress.sharpe_ratio is not None
        assert stress.sortino_ratio is not None

        # Replay crisis drawdowns
        scenarios = stress.macro_shock_scenarios
        assert scenarios["2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)"] != 0.0
        assert scenarios["2020 COVID Crash (SPY -33.7%)"] < -10.0
        assert scenarios["2008 GFC (SPY -37.0%)"] < -10.0
        assert scenarios["2018 Fed Tightening (SPY -19.6%)"] < -5.0

    def test_t4_end_to_end_async_csv_upload_lifecycle(self, test_client):
        """
        T4.2: End-to-end user portfolio ingestion lifecycle:
        1. Upload multipart CSV.
        2. Async background worker parses CSV via asyncio.to_thread.
        3. Portfolio is persisted and analyzed.
        4. Stress metrics returned with mathematical integrity.
        """
        csv_data = (
            "ticker,name,shares,avg_price,current_price,sector\n"
            "MSFT,Microsoft Corp,25,320.0,380.0,Technology\n"
            "UNH,UnitedHealth Group,15,480.0,510.0,Healthcare\n"
            "CVX,Chevron Corp,30,140.0,155.0,Energy\n"
        ).encode("utf-8")

        resp = test_client.post(
            "/api/portfolio/upload",
            files={"file": ("fund_portfolio.csv", csv_data, "text/csv")}
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "success"
        assert "3 holdings" in payload["message"]
        stress = payload["stress"]
        assert "CVX" in [h["ticker"] for h in payload["portfolio"]["holdings"]]
        assert "2022 Tech Rate Shock (SPY -18.2%, QQQ -32.6%)" in stress["macro_shock_scenarios"]

    def test_t4_pipeline_execution_with_mocked_http_transport(self, monkeypatch):
        """
        T4.3: End-to-end orchestrator pipeline execution with HTTP transport mocking
        verifying serialization and prompt-response handling.
        """
        agent = BaseAgent(name="PipelineE2EAgent", role_description="Test pipeline role")
        agent.api_key = "live_gemini_key_stub"

        def mock_post(client_self, url, *args, **kwargs):
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text": json.dumps({
                                "analyses": [{
                                    "ticker": "AAPL",
                                    "impact": "BULLISH",
                                    "magnitude_pct": 3.2,
                                    "rationale": "Strong services growth."
                                }]
                            })
                        }]
                    }
                }]
            }
            return mock_resp

        monkeypatch.setattr(httpx.Client, "post", mock_post)

        res = agent.query_llm_json("Evaluate AAPL earnings")
        assert "analyses" in res
        assert res["analyses"][0]["ticker"] == "AAPL"
        assert res["analyses"][0]["impact"] == "BULLISH"

    def test_t4_ipo_unlisted_portfolio_missing_data_lifecycle(self):
        """
        T4.4: Client imports portfolio containing freshly listed IPO equities with no historical bars.
        The platform guarantees mathematical integrity: returns None for all 9 metrics, logs no excuses,
        and provides exact fields_unavailable disclosure.
        """
        engine = QuantRiskEngine()
        portfolio = Portfolio(
            name="Fresh IPO Portfolio",
            cash=50000.0,
            holdings=[
                PortfolioHolding(ticker="IPO_ALPHA", name="Alpha Tech", shares=1000, avg_price=25.0, current_price=28.0, sector="Technology"),
                PortfolioHolding(ticker="IPO_BETA", name="Beta Bio", shares=500, avg_price=40.0, current_price=42.0, sector="Healthcare"),
            ]
        )
        stress = engine.analyze_portfolio(portfolio, custom_bars_map={})

        assert stress.estimated_portfolio_beta is None
        assert stress.annualized_volatility_pct is None
        assert stress.var_95_daily_pct is None
        assert stress.var_95_daily_usd is None
        assert stress.historical_var_95_pct is None
        assert stress.historical_var_95_usd is None
        assert stress.sharpe_ratio is None
        assert stress.sortino_ratio is None
        assert stress.max_drawdown_pct is None

        exact_msg = "Insufficient historical data for empirical computation (minimum 15 trading days required)"
        assert exact_msg in stress.fields_unavailable
        assert "affine" not in stress.provenance_note.lower()
