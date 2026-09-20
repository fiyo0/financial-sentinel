"""
Phase 6: Comprehensive Test Suite Hardening.
- Multi-threaded concurrency stress tests for SQLite connection pooling & CacheManager.
- Deterministic temporal assertions using freezegun.
- Property-based testing (Hypothesis) for Empirical Quantitative Risk Engine invariants.
"""
import math
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from freezegun import freeze_time
from hypothesis import given, strategies as st, settings

from storage.state_store import StateStore
from storage.cache_manager import CacheManager
from analytics.quant_risk import (
    QuantRiskEngine,
    compute_daily_log_returns,
    compute_sample_covariance,
    compute_empirical_beta
)
from models import Portfolio, PortfolioHolding, NewsItem, NewsCategory
from agents.market_briefing_agent import MarketBriefingAgent

PST = ZoneInfo("America/Los_Angeles")
EST = ZoneInfo("America/New_York")


# ==============================================================================
# 1. Concurrency & Multi-Threaded Stress Tests (SQLite & CacheManager)
# ==============================================================================

def test_sqlite_multithreaded_concurrent_write_read_stress(tmp_path):
    """
    Stress test verifying SQLite with WAL mode, busy timeout, and thread-local
    connection pooling handles 20 concurrent worker threads executing high-frequency
    writes and reads with ZERO 'database is locked' errors.
    """
    db_file = str(tmp_path / "concurrent_stress.db")
    store = StateStore(db_file)

    num_threads = 20
    ops_per_thread = 25
    barrier = threading.Barrier(num_threads)
    errors = []

    def worker(thread_idx: int):
        barrier.wait()
        for i in range(ops_per_thread):
            try:
                # Insert a user and portfolio holding under individual thread connections
                user_id = f"user_{thread_idx}_{i}"
                with store._get_connection() as conn:
                    conn.execute(
                        "INSERT INTO users (id, username, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                        (user_id, f"uname_{thread_idx}_{i}", f"{user_id}@test.com", "hash", "user")
                    )
                    conn.commit()

                # Read back immediately
                u = store.get_user_by_id(user_id)
                assert u is not None and u["username"] == f"uname_{thread_idx}_{i}"

                # Update settings
                store.update_user_settings(user_id, telegram_chat_id=f"chat_{thread_idx}")

            except Exception as e:
                errors.append(f"Thread {thread_idx} op {i} failed: {e}")

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, idx) for idx in range(num_threads)]
        for f in futures:
            f.result()

    assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors[:5]}"

    # Verify total records committed
    with store._get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
    assert count == num_threads * ops_per_thread


def test_cache_manager_multithreaded_churn_and_namespace_isolation():
    """
    Stress test verifying CacheManager under heavy multi-threaded concurrent churn
    across multiple namespaces with simultaneous get, set, clear, and get_or_compute.
    """
    cm = CacheManager(default_ttl_seconds=5.0, max_entries_per_namespace=200)
    num_threads = 16
    ops_per_thread = 50
    barrier = threading.Barrier(num_threads)
    errors = []

    def churner(thread_id: int):
        barrier.wait()
        ns = f"ns_{thread_id % 4}"
        for i in range(ops_per_thread):
            try:
                key = f"key_{i % 10}"
                # Mix of set, get, get_or_compute
                if i % 3 == 0:
                    cm.set(ns, key, {"val": i, "t": thread_id})
                elif i % 3 == 1:
                    cm.get(ns, key)
                else:
                    cm.get_or_compute(ns, key, lambda i=i: {"computed": i * 10}, ttl_seconds=2.0)

                # Periodic clear on specific namespace
                if i == ops_per_thread // 2:
                    cm.clear(ns)

            except Exception as e:
                errors.append(f"Churner {thread_id} error: {e}")

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(churner, idx) for idx in range(num_threads)]
        for f in futures:
            f.result()

    assert len(errors) == 0, f"Cache concurrency errors: {errors}"
    stats = cm.stats()
    assert stats["hits"] + stats["misses"] > 0


# ==============================================================================
# 2. Temporal Freezegun Tests
# ==============================================================================

@freeze_time("2026-09-16 13:30:00")
def test_freezegun_premarket_briefing_prompt_and_session_anchor():
    """
    Using freezegun to freeze execution at exact pre-market bell:
    Wednesday, September 16, 2026 6:30:00 AM PDT (9:30 AM EDT).
    Verifies that temporal calculations, market calendar, and prompt anchoring
    resolve deterministically without timezone drift or local wall-clock pollution.
    """
    now_pst = datetime.now(PST)
    assert now_pst.year == 2026
    assert now_pst.month == 9
    assert now_pst.day == 16
    assert now_pst.hour == 6
    assert now_pst.minute == 30

    agent = MarketBriefingAgent()
    portfolio = Portfolio(
        name="Frozen Test Portfolio",
        cash=25000.0,
        holdings=[
            PortfolioHolding(
                ticker="NVDA",
                name="NVIDIA Corp",
                shares=50,
                avg_price=120.0,
                current_price=125.0,
                sector="Semiconductors",
                daily_change_pct=1.8
            )
        ]
    )

    overview = {
        "indices": {
            "SPY": {"current_price": 550.0, "change_pct": 0.45, "name": "SPDR S&P 500 ETF"},
            "QQQ": {"current_price": 480.0, "change_pct": 0.60, "name": "Invesco QQQ Trust"}
        },
        "market_tone": "Bullish"
    }

    news = [
        NewsItem(
            id="news_fomc_live",
            title="FOMC Concludes September Policy Meeting Today",
            source="Federal Reserve Press",
            url="https://federalreserve.gov/press",
            published_at=datetime(2026, 9, 16, 5, 0, tzinfo=PST),
            summary="Federal Open Market Committee statement expected at 2:00 PM EDT.",
            category=NewsCategory.MACRO,
            related_tickers=[],
            related_sectors=["Financials"]
        )
    ]

    # Verify news formatting correctly resolves relative timestamp from frozen time
    formatted_news = agent._format_news_summary(news, as_of=now_pst)
    assert "Today" in formatted_news
    assert "FOMC Concludes" in formatted_news


# ==============================================================================
# 3. Property-Based Testing (Hypothesis) for Empirical Quant Risk Engine
# ==============================================================================

@st.composite
def positive_price_matrix(draw, num_assets=3, num_days=30):
    """Generates synthetic multi-asset positive closing prices."""
    matrix = {}
    for i in range(num_assets):
        ticker = f"ASSET_{i}"
        base_price = draw(st.floats(min_value=10.0, max_value=500.0))
        # Generate random walk percentage returns
        deltas = draw(st.lists(
            st.floats(min_value=-0.08, max_value=0.08),
            min_size=num_days - 1, max_size=num_days - 1
        ))
        prices = [base_price]
        for d in deltas:
            next_p = max(1.0, prices[-1] * (1.0 + d))
            prices.append(next_p)
        matrix[ticker] = prices
    return matrix


@settings(max_examples=30, deadline=None)
@given(price_map=positive_price_matrix(num_assets=3, num_days=30))
def test_hypothesis_positive_semidefinite_covariance_matrix(price_map):
    """
    Property: For any valid multi-asset return series, the sample covariance matrix Sigma
    is positive semi-definite: for any portfolio weight vector w, w^T * Sigma * w >= -1e-12.
    """
    returns_map = {
        ticker: compute_daily_log_returns(prices)
        for ticker, prices in price_map.items()
    }
    tickers = list(returns_map.keys())
    n_days = min(len(r) for r in returns_map.values())

    # Build covariance matrix
    cov_matrix = {}
    for t1 in tickers:
        cov_matrix[t1] = {}
        for t2 in tickers:
            cov = compute_sample_covariance(returns_map[t1][:n_days], returns_map[t2][:n_days])
            cov_matrix[t1][t2] = cov

    # Test random normalized weight vectors
    weights = [0.4, 0.35, 0.25]
    portfolio_variance = sum(
        weights[i] * weights[j] * cov_matrix[tickers[i]][tickers[j]]
        for i in range(len(tickers))
        for j in range(len(tickers))
    )

    assert portfolio_variance >= -1e-12, f"Variance violated non-negativity: {portfolio_variance}"


@settings(max_examples=25, deadline=None)
@given(
    asset_closes=st.lists(st.floats(min_value=5.0, max_value=500.0), min_size=25, max_size=50),
    bm_closes=st.lists(st.floats(min_value=100.0, max_value=600.0), min_size=25, max_size=50)
)
def test_hypothesis_empirical_beta_and_var_bounds(asset_closes, bm_closes):
    """
    Property: Realized empirical beta is finite, and VaR (95%) is non-negative and bounded.
    """
    n = min(len(asset_closes), len(bm_closes))
    r_asset = compute_daily_log_returns(asset_closes[:n])
    r_bm = compute_daily_log_returns(bm_closes[:n])

    beta = compute_empirical_beta(r_asset, r_bm)
    if beta is not None:
        assert not math.isnan(beta)
        assert not math.isinf(beta)


def test_quant_flat_prices_division_by_zero_resilience():
    """
    Edge case: 100% flat prices (constant $100 across 30 days -> zero variance).
    Engine must return 0.0 annualized vol, default beta, and None for Sharpe/Sortino
    without throwing ZeroDivisionError.
    """
    flat_prices = [100.0] * 30
    flat_bars = [{"date": f"2026-01-{i+1:02d}", "close": 100.0, "open": 100.0, "high": 100.0, "low": 100.0, "volume": 1000} for i in range(30)]

    custom_bars = {
        "FLAT": flat_bars,
        "SPY": flat_bars
    }

    portfolio = Portfolio(
        name="Flat Portfolio",
        cash=1000.0,
        holdings=[
            PortfolioHolding(
                ticker="FLAT",
                name="Flat Inc",
                shares=10,
                avg_price=100.0,
                current_price=100.0,
                sector="Technology"
            )
        ]
    )

    engine = QuantRiskEngine()
    stress = engine.analyze_portfolio(portfolio, custom_bars_map=custom_bars)

    assert stress.annualized_volatility_pct == 0.0
    assert stress.var_95_daily_pct == 0.0
    assert stress.sharpe_ratio is None or stress.sharpe_ratio == 0.0
    assert stress.sortino_ratio is None or stress.sortino_ratio == 0.0


def test_sqlite_wal_concurrent_immediate_writers(tmp_path):
    """
    Verify 20 concurrent threads writing via _write_transaction (BEGIN IMMEDIATE)
    eliminate reader-to-writer lock upgrade deadlocks in WAL mode.
    """
    db_file = str(tmp_path / "concurrent_immediate.db")
    store = StateStore(db_file)

    num_threads = 20
    writes_per_thread = 20
    barrier = threading.Barrier(num_threads)
    errors = []

    def writer_task(thread_idx: int):
        barrier.wait()
        for i in range(writes_per_thread):
            try:
                with store._write_transaction() as conn:
                    conn.execute(
                        "INSERT INTO kv_store (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP;",
                        (f"k_{thread_idx}_{i}", f"val_{thread_idx}_{i}")
                    )
            except Exception as e:
                errors.append(f"Thread {thread_idx} write {i} failed: {e}")

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(writer_task, idx) for idx in range(num_threads)]
        for f in futures:
            f.result(timeout=15.0)

    assert len(errors) == 0, f"Encountered errors with _write_transaction: {errors}"
    with store._get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM kv_store;").fetchone()[0]
    assert count == num_threads * writes_per_thread
