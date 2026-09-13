"""
Deterministic Technical Analysis & Momentum Engine.
Computes mathematically verified technical indicators (RSI-14, MACD, Bollinger Bands, Moving Averages, ATR)
from verified daily price bars with zero LLM math hallucination.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import math
import time
import logging
import httpx
from analytics.provenance import Provenance

logger = logging.getLogger(__name__)

# In-memory TTL cache for daily historical bars: ticker -> (timestamp, bars)
BARS_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
BARS_SOURCE_MAP: Dict[str, str] = {}
BARS_CACHE_TTL_SECONDS = 900.0  # 15 minutes TTL


@dataclass
class TechnicalSnapshot:
    ticker: str
    current_price: float
    # RSI
    rsi_14: Optional[float] = None
    rsi_status: str = "NEUTRAL"
    # MACD
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_status: str = "NEUTRAL"
    # Bollinger Bands
    bollinger_upper: Optional[float] = None
    bollinger_middle: Optional[float] = None
    bollinger_lower: Optional[float] = None
    bollinger_pct_b: Optional[float] = None
    bollinger_bandwidth: Optional[float] = None
    bollinger_status: str = "NORMAL"
    # Moving Averages
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    dist_from_50_dma_pct: Optional[float] = None
    dist_from_200_dma_pct: Optional[float] = None
    trend_alignment: str = "MIXED_CONSOLIDATION"
    # Volatility / Trailing Stop
    atr_14: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    rvol: float = 1.0
    avg_volume_20: int = 0
    is_live: bool = True
    error: Optional[str] = None
    provenance: Optional[Provenance] = None

    def to_summary_line(self) -> str:
        """Compact single-line technical pulse for telegram and executive briefs."""
        if not self.is_live:
            return "Technical Indicators: Unavailable"

        cross_str = f" | {self.trend_alignment}" if "CROSS" in self.trend_alignment else ""
        dma_50_part = f"50 DMA: {self.dist_from_50_dma_pct:+.1f}%" if self.dist_from_50_dma_pct is not None else "50 DMA: —"
        dma_200_part = f" | 200 DMA: {self.dist_from_200_dma_pct:+.1f}%" if self.dist_from_200_dma_pct is not None else ""
        rsi_part = f"RSI-14: {self.rsi_14:.1f} ({self.rsi_status})" if self.rsi_14 is not None else "RSI-14: —"
        macd_part = f"MACD: {self.macd_status}" if self.macd_line is not None else "MACD: —"
        bb_part = f"BB %B: {self.bollinger_pct_b:.2f}" if self.bollinger_pct_b is not None else "BB: —"
        return f"{rsi_part} | {macd_part} | {dma_50_part}{dma_200_part} | {bb_part}{cross_str}"

    def to_telegram_block(self) -> str:
        """Rich HTML formatted section for Telegram."""
        if not self.is_live:
            return "📈 <i>Technical momentum data currently unavailable.</i>"

        if self.rsi_14 is not None:
            rsi_emoji = "🔴" if self.rsi_14 >= 70 else ("🟢" if self.rsi_14 <= 30 else "⚪")
            rsi_line = f"• <b>RSI (14-Day):</b> {rsi_emoji} <code>{self.rsi_14:.1f}</code> ({self.rsi_status.replace('_', ' ')})"
        else:
            rsi_line = "• <b>RSI (14-Day):</b> ⚪ <code>— (&lt;14 bars)</code>"

        if self.macd_line is not None and self.macd_hist is not None:
            macd_emoji = "🟢" if "BULLISH" in self.macd_status else ("🔴" if "BEARISH" in self.macd_status else "⚪")
            macd_line = f"• <b>MACD (12, 26, 9):</b> {macd_emoji} <code>{self.macd_status.replace('_', ' ')}</code> (Hist: {self.macd_hist:+.2f})"
        else:
            macd_line = "• <b>MACD (12, 26, 9):</b> ⚪ <code>— (&lt;35 bars)</code>"

        if self.sma_50 is not None and self.dist_from_50_dma_pct is not None:
            dma_50_sign = "+" if self.dist_from_50_dma_pct >= 0 else ""
            dma_50_str = f"50 DMA: <code>${self.sma_50:.2f}</code> ({dma_50_sign}{self.dist_from_50_dma_pct:.1f}%)"
        else:
            dma_50_str = "50 DMA: <code>— (&lt;50 bars)</code>"

        if self.sma_200 is not None and self.dist_from_200_dma_pct is not None:
            dma_200_sign = "+" if self.dist_from_200_dma_pct >= 0 else ""
            dma_200_str = f"200 DMA: <code>${self.sma_200:.2f}</code> ({dma_200_sign}{self.dist_from_200_dma_pct:.1f}%)"
        else:
            dma_200_str = "200 DMA: <code>N/A (&lt;200 bars)</code>"

        if self.bollinger_lower is not None and self.bollinger_upper is not None and self.bollinger_pct_b is not None:
            squeeze_alert = " ⚠️ <i>[Volatility Squeeze in Progress]</i>" if self.bollinger_status == "VOLATILITY_SQUEEZE" else ""
            bb_line = f"• <b>Bollinger Bands:</b> <code>${self.bollinger_lower:.2f}</code> – <code>${self.bollinger_upper:.2f}</code> (%B: {self.bollinger_pct_b:.2f}){squeeze_alert}"
        else:
            bb_line = "• <b>Bollinger Bands:</b> ⚪ <code>— (&lt;20 bars)</code>"

        if self.suggested_stop_loss is not None and self.atr_14 is not None:
            vol_line = f"• <b>Dynamic ATR Volatility Stop Band:</b> <code>${self.suggested_stop_loss:.2f}</code> (ATR-14: ${self.atr_14:.2f})"
        else:
            vol_line = "• <b>Dynamic ATR Volatility Stop Band:</b> ⚪ <code>—</code>"

        return (
            f"📈 <b>Technical Momentum & Trend Health:</b>\n"
            f"{rsi_line}\n"
            f"{macd_line}\n"
            f"• <b>Moving Averages:</b> {dma_50_str} | {dma_200_str}\n"
            f"{bb_line}\n"
            f"{vol_line}"
        )

    def to_prompt_context(self, price: Optional[float] = None) -> str:
        """Structured text summary formatted for LLM prompts."""
        if not self.is_live:
            return "VERIFIED TECHNICAL MOMENTUM DATA: Unavailable (<20 historical bars)."
        p = price or self.current_price

        rsi_str = f"{self.rsi_14:.1f} ({self.rsi_status})" if self.rsi_14 is not None else "N/A (<14 bars)"
        if self.macd_line is not None and self.macd_signal is not None and self.macd_hist is not None:
            macd_str = f"Line {self.macd_line:+.2f} | Signal {self.macd_signal:+.2f} | Hist {self.macd_hist:+.2f} ({self.macd_status})"
        else:
            macd_str = "N/A (<35 bars)"

        dma_50_str = f"${self.sma_50:.2f} ({self.dist_from_50_dma_pct:+.1f}%)" if self.sma_50 is not None and self.dist_from_50_dma_pct is not None else "N/A (<50 bars)"
        dma_200_str = f"${self.sma_200:.2f} ({self.dist_from_200_dma_pct:+.1f}%)" if self.sma_200 is not None and self.dist_from_200_dma_pct is not None else "N/A (<200 bars)"

        if self.bollinger_lower is not None and self.bollinger_upper is not None and self.bollinger_pct_b is not None and self.bollinger_bandwidth is not None:
            bb_str = f"${self.bollinger_lower:.2f} to ${self.bollinger_upper:.2f} (%B: {self.bollinger_pct_b:.2f}, Bandwidth: {self.bollinger_bandwidth:.1f}%, Status: {self.bollinger_status})"
        else:
            bb_str = "N/A (<20 bars)"

        if self.atr_14 is not None and self.suggested_stop_loss is not None:
            vol_str = f"${self.atr_14:.2f} | Suggested Trailing Stop Floor: ${self.suggested_stop_loss:.2f}"
        else:
            vol_str = "N/A (<15 bars)"

        prov_src = f" [Source: {self.provenance.source}]" if self.provenance else ""
        return f"""
        VERIFIED TECHNICAL MOMENTUM DATA (CALCULATED FROM PRICE BARS{prov_src}):
        • Live Price: ${p:.2f}
        • RSI (14-Day): {rsi_str}
        • MACD (12, 26, 9): {macd_str}
        • Moving Averages: 50 DMA {dma_50_str} | 200 DMA {dma_200_str}
        • Bollinger Bands: {bb_str}
        • Volatility ATR (14-Day): {vol_str}
        """


def _calc_ema(values: List[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average across a series, seeded with initial SMA."""
    if not values or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    sma = sum(values[:period]) / float(period)
    res = [sma]
    ema = sma
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
        res.append(ema)
    return res


def _fetch_historical_bars(ticker: str, force_fresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches daily price bars (~250 trading days) for technical analysis.
    Uses Robinhood marketdata historicals endpoint with fallback to Yahoo chart.
    Cached for 15 minutes to eliminate redundant network overhead.
    """
    clean_ticker = ticker.strip().upper()
    now = time.time()
    if not force_fresh and clean_ticker in BARS_CACHE:
        ts, cached_bars = BARS_CACHE[clean_ticker]
        if now - ts < BARS_CACHE_TTL_SECONDS and len(cached_bars) >= 20:
            return cached_bars

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    # 1. Primary: Robinhood Marketdata Historicals (1 year daily)
    rh_url = f"https://api.robinhood.com/marketdata/historicals/{clean_ticker}/?interval=day&span=year"
    try:
        resp = httpx.get(rh_url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            bars = data.get("historicals", [])
            if len(bars) >= 20:
                parsed_bars = [
                    {
                        "date": b.get("begins_at"),
                        "close": float(b.get("close_price") or 0.0),
                        "high": float(b.get("high_price") or 0.0),
                        "low": float(b.get("low_price") or 0.0),
                        "open": float(b.get("open_price") or 0.0),
                        "volume": int(b.get("volume") or 0)
                    }
                    for b in bars
                    if float(b.get("close_price") or 0.0) > 0
                ]
                if len(parsed_bars) >= 20:
                    BARS_CACHE[clean_ticker] = (now, parsed_bars)
                    BARS_SOURCE_MAP[clean_ticker] = "robinhood"
                    return parsed_bars
    except Exception as e:
        logger.debug(f"Primary historicals fetch failed for {clean_ticker}: {e}")

    # 2. Fallback: Yahoo Finance Chart API (6 months daily)
    y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_ticker}?interval=1d&range=6mo"
    try:
        resp = httpx.get(y_url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            quotes = result.get("indicators", {}).get("quote", [{}])[0]
            closes = quotes.get("close", [])
            highs = quotes.get("high", [])
            lows = quotes.get("low", [])
            opens = quotes.get("open", [])
            volumes = quotes.get("volume", [])

            bars = []
            for i in range(len(closes)):
                c = closes[i]
                if c is not None and c > 0:
                    bars.append({
                        "date": str(i),
                        "close": float(c),
                        "high": float(highs[i] if i < len(highs) and highs[i] else c),
                        "low": float(lows[i] if i < len(lows) and lows[i] else c),
                        "open": float(opens[i] if i < len(opens) and opens[i] else c),
                        "volume": int(volumes[i] if i < len(volumes) and volumes[i] else 0)
                    })
            if len(bars) >= 20:
                BARS_CACHE[clean_ticker] = (now, bars)
                BARS_SOURCE_MAP[clean_ticker] = "yahoo"
                return bars
    except Exception as e:
        logger.debug(f"Fallback historicals fetch failed for {clean_ticker}: {e}")

    return []


def compute_technical_snapshot(ticker: str, custom_bars: Optional[List[Dict[str, Any]]] = None) -> TechnicalSnapshot:
    """
    Computes deterministic technical momentum and trend indicators for any ticker.
    Enforces strict missing-data rules: indicators with insufficient historical data
    return None and are tracked in the Provenance envelope.
    """
    clean_ticker = ticker.strip().upper()
    if custom_bars is not None:
        bars = custom_bars
        source = "custom"
    else:
        bars = _fetch_historical_bars(clean_ticker)
        source = BARS_SOURCE_MAP.get(clean_ticker, "robinhood")

    fields_unavailable: List[str] = []
    fields_estimated: List[str] = []

    if not bars or len(bars) < 14:
        unavail = ("rsi_14", "macd", "bollinger", "sma_20", "sma_50", "sma_200", "atr_14")
        prov = Provenance.create(
            source=source,
            bar_count=len(bars) if bars else 0,
            fields_unavailable=unavail,
            fields_estimated=()
        )
        closes = [b["close"] for b in bars] if bars else []
        c_price = round(closes[-1], 2) if closes else 0.0
        return TechnicalSnapshot(
            ticker=clean_ticker,
            current_price=c_price,
            rsi_14=None,
            rsi_status="INSUFFICIENT_DATA",
            macd_line=None,
            macd_signal=None,
            macd_hist=None,
            macd_status="INSUFFICIENT_DATA",
            bollinger_upper=None,
            bollinger_middle=None,
            bollinger_lower=None,
            bollinger_pct_b=None,
            bollinger_bandwidth=None,
            bollinger_status="INSUFFICIENT_DATA",
            sma_20=None,
            sma_50=None,
            sma_200=None,
            dist_from_50_dma_pct=None,
            dist_from_200_dma_pct=None,
            trend_alignment="INSUFFICIENT_DATA",
            atr_14=None,
            suggested_stop_loss=None,
            is_live=False,
            error=f"Insufficient historical trading data ({len(bars) if bars else 0} bars)",
            provenance=prov
        )

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    current_price = closes[-1]
    n_bars = len(closes)

    # 1. RSI-14 with Wilder's Smoothing
    deltas = [closes[i] - closes[i - 1] for i in range(1, n_bars)]
    gains = [max(0.0, d) for d in deltas]
    losses = [max(0.0, -d) for d in deltas]

    period = 14
    avg_gain = sum(gains[:period]) / float(period)
    avg_loss = sum(losses[:period]) / float(period)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / float(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / float(period)

    if avg_gain == 0.0 and avg_loss == 0.0:
        rsi_14 = 50.0
        rsi_status = "NEUTRAL"
        fields_estimated.append("rsi_14_flat_series")
    else:
        rs = avg_gain / (avg_loss if avg_loss > 0 else 1e-9)
        rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 2)

        if rsi_14 >= 75.0:
            rsi_status = "EXTREME_OVERBOUGHT"
        elif rsi_14 >= 70.0:
            rsi_status = "OVERBOUGHT"
        elif rsi_14 <= 25.0:
            rsi_status = "EXTREME_OVERSOLD"
        elif rsi_14 <= 30.0:
            rsi_status = "OVERSOLD"
        elif rsi_14 >= 55.0:
            rsi_status = "NEUTRAL_BULLISH"
        elif rsi_14 <= 45.0:
            rsi_status = "NEUTRAL_BEARISH"
        else:
            rsi_status = "NEUTRAL"

    # 2. Moving Averages: SMA 20, SMA 50, SMA 200
    if n_bars >= 20:
        sma_20 = round(sum(closes[-20:]) / 20.0, 2)
    else:
        sma_20 = None
        fields_unavailable.append("sma_20")

    if n_bars >= 50:
        sma_50 = round(sum(closes[-50:]) / 50.0, 2)
        dist_from_50 = round(((current_price - sma_50) / sma_50) * 100.0, 2)
    else:
        sma_50 = None
        dist_from_50 = None
        fields_unavailable.append("sma_50")

    if n_bars >= 200:
        sma_200 = round(sum(closes[-200:]) / 200.0, 2)
        dist_from_200 = round(((current_price - sma_200) / sma_200) * 100.0, 2)
    else:
        sma_200 = None
        dist_from_200 = None
        fields_unavailable.append("sma_200")

    if sma_50 is not None and sma_200 is not None:
        if current_price > sma_50 > sma_200:
            trend_alignment = "STRONG_BULLISH"
        elif current_price < sma_50 < sma_200:
            trend_alignment = "STRONG_BEARISH"
        elif current_price > sma_50:
            trend_alignment = "MODERATE_BULLISH"
        elif current_price < sma_50:
            trend_alignment = "BEARISH_CORRECTION"
        else:
            trend_alignment = "MIXED_CONSOLIDATION"
    elif sma_50 is not None:
        if current_price > sma_50:
            trend_alignment = "MODERATE_BULLISH"
        elif current_price < sma_50:
            trend_alignment = "BEARISH_CORRECTION"
        else:
            trend_alignment = "MIXED_CONSOLIDATION"
    else:
        trend_alignment = "INSUFFICIENT_DATA"

    # 3. Bollinger Bands (20-day SMA ± 2 standard deviations)
    if n_bars >= 20:
        lookback_20 = closes[-20:]
        mean_20 = sum(lookback_20) / 20.0
        var_20 = sum((x - mean_20) ** 2 for x in lookback_20) / 20.0
        std_20 = math.sqrt(var_20)

        raw_upper = mean_20 + 2.0 * std_20
        raw_lower = mean_20 - 2.0 * std_20
        raw_range = raw_upper - raw_lower
        raw_pct_b = (current_price - raw_lower) / raw_range if raw_range > 1e-9 else 0.5
        raw_bandwidth = (raw_range / mean_20) * 100.0 if mean_20 > 1e-9 else 0.0

        bollinger_upper = round(raw_upper, 2)
        bollinger_lower = round(raw_lower, 2)
        bollinger_middle = round(mean_20, 2)
        bollinger_pct_b = round(raw_pct_b, 2)
        bollinger_bandwidth = round(raw_bandwidth, 2)

        if current_price >= raw_upper:
            bollinger_status = "UPPER_BAND_EXTENDED"
        elif current_price <= raw_lower:
            bollinger_status = "LOWER_BAND_EXTENDED"
        elif raw_bandwidth < 4.5:
            bollinger_status = "VOLATILITY_SQUEEZE"
        else:
            bollinger_status = "NORMAL"
    else:
        bollinger_upper = None
        bollinger_middle = None
        bollinger_lower = None
        bollinger_pct_b = None
        bollinger_bandwidth = None
        bollinger_status = "INSUFFICIENT_DATA"
        fields_unavailable.append("bollinger")

    # 4. MACD (12 EMA, 26 EMA, 9 Signal EMA)
    if n_bars >= 35:
        ema_12 = _calc_ema(closes, 12)
        ema_26 = _calc_ema(closes, 26)
        offset = len(ema_12) - len(ema_26)
        macd_series = [e12 - e26 for e12, e26 in zip(ema_12[offset:], ema_26)]
        signal_series = _calc_ema(macd_series, 9)

        if len(signal_series) >= 2:
            macd_line = round(macd_series[-1], 3)
            macd_signal = round(signal_series[-1], 3)
            macd_hist = round(macd_line - macd_signal, 3)

            prev_macd = macd_series[-2]
            prev_signal = signal_series[-2]

            if prev_macd <= prev_signal and macd_line > macd_signal:
                macd_status = "BULLISH_CROSSOVER"
            elif prev_macd >= prev_signal and macd_line < macd_signal:
                macd_status = "BEARISH_DIVERGENCE"
            elif macd_hist > 0 and macd_hist > (prev_macd - prev_signal):
                macd_status = "BULLISH_ACCELERATING"
            elif macd_hist < 0 and macd_hist < (prev_macd - prev_signal):
                macd_status = "BEARISH_ACCELERATING"
            elif macd_line > 0:
                macd_status = "BULLISH_TERRITORY"
            else:
                macd_status = "BEARISH_TERRITORY"
        else:
            macd_line, macd_signal, macd_hist = None, None, None
            macd_status = "INSUFFICIENT_DATA"
            fields_unavailable.append("macd")
    else:
        macd_line, macd_signal, macd_hist = None, None, None
        macd_status = "INSUFFICIENT_DATA"
        fields_unavailable.append("macd")

    # 5. ATR-14 with true Wilder's Smoothing
    tr_values = []
    for i in range(1, n_bars):
        h = highs[i]
        lo = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - lo, abs(h - prev_c), abs(lo - prev_c))
        tr_values.append(tr)

    if len(tr_values) >= 14:
        atr_val = sum(tr_values[:14]) / 14.0
        for tr in tr_values[14:]:
            atr_val = (atr_val * 13.0 + tr) / 14.0
        atr_14 = round(atr_val, 2)
        suggested_stop_loss = round(max(0.0, current_price - (2.0 * atr_14)), 2)
    else:
        atr_14 = None
        suggested_stop_loss = None
        fields_unavailable.append("atr_14")

    # 6. Relative Volume (RVOL) & 20-Day Average Volume
    valid_vol_bars = [b for b in bars if (b.get("volume") or 0) > 0]
    rvol = 1.0
    avg_volume_20 = 0
    if len(valid_vol_bars) >= 21:
        latest_vol = valid_vol_bars[-1]["volume"]
        avg_volume_20 = int(sum(b["volume"] for b in valid_vol_bars[-21:-1]) / 20.0)
        rvol = round(float(latest_vol) / float(avg_volume_20), 2) if avg_volume_20 > 0 else 1.0
    elif len(valid_vol_bars) > 1:
        latest_vol = valid_vol_bars[-1]["volume"]
        prev_bars = valid_vol_bars[:-1]
        avg_volume_20 = int(sum(b["volume"] for b in prev_bars) / float(len(prev_bars)))
        rvol = round(float(latest_vol) / float(avg_volume_20), 2) if avg_volume_20 > 0 else 1.0

    provenance = Provenance.create(
        source=source,
        bar_count=n_bars,
        fields_unavailable=tuple(fields_unavailable),
        fields_estimated=tuple(fields_estimated)
    )

    return TechnicalSnapshot(
        ticker=clean_ticker,
        current_price=round(current_price, 2),
        rsi_14=rsi_14,
        rsi_status=rsi_status,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        macd_status=macd_status,
        bollinger_upper=bollinger_upper,
        bollinger_middle=bollinger_middle,
        bollinger_lower=bollinger_lower,
        bollinger_pct_b=bollinger_pct_b,
        bollinger_bandwidth=bollinger_bandwidth,
        bollinger_status=bollinger_status,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        dist_from_50_dma_pct=dist_from_50,
        dist_from_200_dma_pct=dist_from_200,
        trend_alignment=trend_alignment,
        atr_14=atr_14,
        suggested_stop_loss=suggested_stop_loss,
        rvol=rvol,
        avg_volume_20=avg_volume_20,
        is_live=True,
        provenance=provenance
    )
