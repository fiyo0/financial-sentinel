"""
Unit tests for analytics/technical_indicators.py.
Verifies mathematical correctness of RSI-14, MACD, Bollinger Bands, Moving Averages, and ATR.
"""
from unittest.mock import patch, MagicMock
from analytics.technical_indicators import (
    compute_technical_snapshot,
    _calc_ema,
    _fetch_historical_bars,
)


def generate_bars(n: int = 60, start_price: float = 100.0, trend: float = 1.0):
    """Generates synthetic daily price bars with known upward or downward trend."""
    bars = []
    price = start_price
    for i in range(n):
        price += trend
        high = price + 1.5
        low = max(1.0, price - 1.5)
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": price - 0.5,
            "high": high,
            "low": low,
            "close": price,
            "volume": 1000000 + i * 5000,
        })
    return bars


def test_calc_ema():
    values = [10.0, 11.0, 12.0, 13.0, 14.0]
    ema = _calc_ema(values, 3)
    # With SMA seed (H-10), starts at period with SMA(10, 11, 12) = 11.0
    assert len(ema) == 3
    assert ema[0] == 11.0
    # k = 2 / (3 + 1) = 0.5
    # ema[1] = 13.0 * 0.5 + 11.0 * 0.5 = 12.0
    assert abs(ema[1] - 12.0) < 1e-4
    # ema[2] = 14.0 * 0.5 + 12.0 * 0.5 = 13.0
    assert abs(ema[2] - 13.0) < 1e-4


def test_insufficient_bars():
    short_bars = generate_bars(n=10)
    snap = compute_technical_snapshot("TEST", custom_bars=short_bars)
    assert snap.is_live is False
    assert snap.rsi_14 == 50.0
    assert "Insufficient historical" in (snap.error or "")
    assert "Unavailable" in snap.to_summary_line()
    assert "currently unavailable" in snap.to_telegram_block()


def test_bullish_momentum_math():
    bull_bars = generate_bars(n=60, start_price=100.0, trend=1.0)
    snap = compute_technical_snapshot("NVDA", custom_bars=bull_bars)

    assert snap.is_live is True
    assert snap.ticker == "NVDA"
    assert snap.current_price == 160.0
    assert snap.rsi_14 > 70.0  # Steady gains produce overbought RSI
    assert snap.rsi_status in ["OVERBOUGHT", "EXTREME_OVERBOUGHT"]
    assert snap.dist_from_50_dma_pct > 0
    # With 60 bars (<200), sma_200 must be None (H-9)
    assert snap.sma_200 is None
    assert snap.dist_from_200_dma_pct is None

    # Test >= 200 bars gives valid sma_200
    long_bars = generate_bars(n=220, start_price=100.0, trend=1.0)
    snap_long = compute_technical_snapshot("NVDA", custom_bars=long_bars)
    assert snap_long.sma_200 is not None
    assert snap_long.dist_from_200_dma_pct > 0
    assert snap.trend_alignment in ["STRONG_BULLISH", "MODERATE_BULLISH"]
    assert snap.bollinger_upper > snap.bollinger_lower
    assert snap.suggested_stop_loss < snap.current_price

    summary = snap.to_summary_line()
    assert "RSI-14:" in summary
    assert "MACD:" in summary
    assert "50 DMA:" in summary

    tg = snap.to_telegram_block()
    assert "Technical Momentum" in tg
    assert "RSI (14-Day):" in tg
    assert "Bollinger Bands:" in tg


def test_bearish_momentum_math():
    bear_bars = generate_bars(n=60, start_price=200.0, trend=-1.5)
    snap = compute_technical_snapshot("TSLA", custom_bars=bear_bars)

    assert snap.is_live is True
    assert snap.ticker == "TSLA"
    assert snap.current_price == 110.0
    assert snap.rsi_14 < 35.0  # Steady losses produce oversold RSI
    assert snap.rsi_status in ["OVERSOLD", "EXTREME_OVERSOLD"]
    assert snap.dist_from_50_dma_pct < 0
    assert snap.trend_alignment in ["STRONG_BEARISH", "BEARISH_CORRECTION"]


@patch("httpx.get")
def test_fetch_historical_bars_robinhood_mock(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "historicals": [
            {"begins_at": "2026-01-01", "close_price": "150.0", "high_price": "152.0", "low_price": "148.0", "open_price": "149.0", "volume": 1000}
            for _ in range(30)
        ]
    }
    mock_get.return_value = mock_resp

    bars = _fetch_historical_bars("AAPL")
    assert len(bars) == 30
    assert bars[0]["close"] == 150.0
