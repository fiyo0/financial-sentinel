"""
Property-based and golden-file tests for deterministic technical indicators.
Uses Hypothesis to verify mathematical invariants and Wilder reference benchmarks.
"""
from hypothesis import given, strategies as st, settings
from analytics.technical_indicators import compute_technical_snapshot


@st.composite
def price_series(draw, min_size=15, max_size=100):
    """Generates synthetic valid positive daily bar series."""
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    closes = draw(st.lists(
        st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
        min_size=size, max_size=size
    ))
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 100000
        })
    return bars


@settings(max_examples=50, deadline=None)
@given(bars=price_series(min_size=15, max_size=80))
def test_property_rsi_bounded(bars):
    """Property: For any valid price series with >= 14 bars, RSI is bounded between 0 and 100."""
    snap = compute_technical_snapshot("TEST", custom_bars=bars)
    assert snap.rsi_14 is not None
    assert 0.0 <= snap.rsi_14 <= 100.0
    assert snap.provenance is not None
    assert snap.provenance.bar_count == len(bars)


@settings(max_examples=50, deadline=None)
@given(bars=price_series(min_size=20, max_size=80))
def test_property_bollinger_band_ordering(bars):
    """Property: Bollinger lower band <= middle band <= upper band for any series >= 20 bars."""
    snap = compute_technical_snapshot("TEST", custom_bars=bars)
    assert snap.bollinger_lower is not None
    assert snap.bollinger_middle is not None
    assert snap.bollinger_upper is not None
    assert snap.bollinger_lower <= snap.bollinger_middle <= snap.bollinger_upper


@settings(max_examples=30, deadline=None)
@given(
    bars=price_series(min_size=20, max_size=50),
    offset=st.floats(min_value=10.0, max_value=500.0, allow_nan=False, allow_infinity=False)
)
def test_property_rsi_translation_invariance(bars, offset):
    """Property: Adding a constant offset to all prices preserves exact RSI deltas."""
    snap_base = compute_technical_snapshot("BASE", custom_bars=bars)
    shifted_bars = [{**b, "close": b["close"] + offset, "high": b["high"] + offset, "low": b["low"] + offset} for b in bars]
    snap_shifted = compute_technical_snapshot("SHIFTED", custom_bars=shifted_bars)

    assert snap_base.rsi_14 is not None
    assert snap_shifted.rsi_14 is not None
    # Tolerance for small floating point variations
    assert abs(snap_base.rsi_14 - snap_shifted.rsi_14) <= 0.15


@settings(max_examples=30, deadline=None)
@given(bars=price_series(min_size=1, max_size=13))
def test_property_insufficient_bars_returns_none(bars):
    """Property: Series with < 14 bars strictly returns rsi_14=None and records in provenance."""
    snap = compute_technical_snapshot("SHORT", custom_bars=bars)
    assert snap.rsi_14 is None
    assert snap.is_live is False
    assert snap.provenance is not None
    assert "rsi_14" in snap.provenance.fields_unavailable


def test_wilder_rsi_golden_reference():
    """
    Golden-file reference: J. Welles Wilder's 14-day worked RSI example
    from 'New Concepts in Technical Trading Systems' (1978).
    """
    wilder_prices = [
        46.125, 47.125, 46.45, 46.95, 47.85, 47.375, 47.00,
        47.90, 48.00, 47.50, 47.375, 46.70, 46.80, 46.80, 46.50
    ]
    bars = [
        {"date": f"D{i+1}", "open": p, "high": p + 0.5, "low": p - 0.5, "close": p, "volume": 1000}
        for i, p in enumerate(wilder_prices)
    ]
    snap = compute_technical_snapshot("WILDER", custom_bars=bars)
    assert snap.rsi_14 is not None
    # Wilder calculated first smoothed RSI at bar 15: avg_gain=0.25, avg_loss=0.223, RS=1.12 -> RSI=52.83 (Wilder 1978, p. 66)
    assert abs(snap.rsi_14 - 52.83) <= 0.05

