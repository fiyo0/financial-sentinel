"""
Multi-Source Social & Retail Sentiment Ingestion Stream.
Combines StockTwits public symbol stream (with true time velocity and keyword-augmented sentiment)
and Reddit (r/wallstreetbets, r/stocks via Google News RSS index) with Relative Trading Volume (RVOL)
to compute mathematically and empirically grounded retail sentiment velocity.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import urllib.parse
import re
import html
import logging
import httpx
import feedparser

logger = logging.getLogger(__name__)

REDDIT_UA = "financial-sentinel/1.0 (+https://github.com/frank/financial-sentinel)"
STOCKTWITS_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

_STOCKTWITS_CLIENT: Optional[httpx.Client] = None
try:
    import h2  # noqa
    _HAS_HTTP2 = True
except ImportError:
    _HAS_HTTP2 = False

def get_stocktwits_client() -> httpx.Client:
    global _STOCKTWITS_CLIENT
    if _STOCKTWITS_CLIENT is None or _STOCKTWITS_CLIENT.is_closed:
        _STOCKTWITS_CLIENT = httpx.Client(
            http2=_HAS_HTTP2,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
            timeout=5.0
        )
    return _STOCKTWITS_CLIENT

BULL_KW = re.compile(r"\b(buy|bought|calls|long|moon|breakout|green|rocket|undervalued|rally|accumulate|support|dip)\b", re.I)
BEAR_KW = re.compile(r"\b(sell|sold|puts|short|dump|drop|red|crash|scam|overvalued|tank|fall|bear|downgrade|bubble|loss)\b", re.I)


@dataclass
class SentimentSnapshot:
    ticker: str
    retail_bull_pct: float
    retail_bear_pct: float
    total_messages_analyzed: int
    social_velocity: str  # SURGING, ELEVATED, STEADY, COOLING, DORMANT, LOW
    reddit_post_count: int
    messages_per_hour: float = 0.0
    acceleration_factor: float = 1.0
    span_hours: float = 24.0
    sample_reddit_headlines: List[str] = field(default_factory=list)
    sample_stocktwits_comments: List[str] = field(default_factory=list)
    relative_volume: float = 1.0  # RVOL (1.0 = normal)
    composite_sentiment_score: float = 50.0  # 0 to 100
    sentiment_verdict: str = "NEUTRAL_BALANCED"
    is_live: bool = True
    error: Optional[str] = None

    def format_recency_window(self) -> str:
        if self.span_hours < 1.0:
            mins = max(1, int(self.span_hours * 60))
            return f"{mins}m"
        elif self.span_hours < 48.0:
            return f"{self.span_hours:.1f}h"
        else:
            days = self.span_hours / 24.0
            return f"{days:.1f}d"

    def to_summary_line(self) -> str:
        if not self.is_live:
            return "Social Sentiment: Unavailable"
        recency = self.format_recency_window()
        rate_str = f" ({self.messages_per_hour:.1f}/hr · {self.total_messages_analyzed} in {recency} · {self.acceleration_factor:.1f}x)" if (self.messages_per_hour > 0 or self.total_messages_analyzed > 0) else ""
        return (
            f"Social: {self.retail_bull_pct:.0f}% Bullish ({self.sentiment_verdict.replace('_', ' ')}) | "
            f"Velocity: {self.social_velocity}{rate_str} | RVOL: {self.relative_volume:.2f}x"
        )

    def to_telegram_block(self) -> str:
        if not self.is_live:
            return "💬 <i>Social sentiment stream currently unavailable.</i>"

        emoji = "🟢" if self.composite_sentiment_score >= 65 else ("🔴" if self.composite_sentiment_score <= 35 else "⚪")
        
        recency = self.format_recency_window()
        accel_str = f" · {self.acceleration_factor:.1f}x accel" if self.acceleration_factor > 0 else ""
        velocity_detail = f"{self.messages_per_hour:.1f} msgs/hr · {self.total_messages_analyzed} in {recency}{accel_str}" if self.messages_per_hour > 0 else f"{self.total_messages_analyzed} posts"
        reddit_detail = f", {self.reddit_post_count} Reddit threads" if self.reddit_post_count > 0 else ""

        return (
            f"💬 <b>Retail Social Sentiment & Velocity:</b>\n"
            f"• <b>Crowd Sentiment:</b> {emoji} <code>{self.retail_bull_pct:.0f}% Bullish</code> "
            f"({self.sentiment_verdict.replace('_', ' ')})\n"
            f"• <b>Activity Velocity:</b> <code>{self.social_velocity}</code> "
            f"({velocity_detail} on StockTwits{reddit_detail})\n"
            f"• <b>Relative Trading Volume (RVOL):</b> <code>{self.relative_volume:.2f}x</code>"
        )


def _fetch_stocktwits_stream(ticker: str) -> Dict[str, Any]:
    """
    Fetches real-time retail messages from StockTwits public symbol stream.
    Calculates true message arrival rate per hour and weights untagged chatter with keyword heuristics.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    headers = {"User-Agent": STOCKTWITS_UA}
    try:
        resp = get_stocktwits_client().get(url, headers=headers, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            messages = data.get("messages", [])
            bulls = 0.0
            bears = 0.0
            sample_comments = []

            for m in messages:
                body = m.get("body", "")
                entities = m.get("entities") or {}
                sent_obj = entities.get("sentiment")
                
                # Check manual tags
                if isinstance(sent_obj, dict):
                    basic = sent_obj.get("basic")
                    if basic == "Bullish":
                        bulls += 1.0
                    elif basic == "Bearish":
                        bears += 1.0
                else:
                    # Parse body for keyword sentiment
                    has_bull = bool(BULL_KW.search(body))
                    has_bear = bool(BEAR_KW.search(body))
                    if has_bull and not has_bear:
                        bulls += 0.5
                    elif has_bear and not has_bull:
                        bears += 0.5

                # Collect clean sample comments
                if body and len(sample_comments) < 3 and len(body) > 20:
                    clean_body = re.sub(r'https?://\S+', '', body).strip()
                    if clean_body and "$" in clean_body or len(clean_body) > 30:
                        sample_comments.append(clean_body[:100])

            # Calculate True Time Velocity (Arrival Rate per Hour) & Intra-Stream Acceleration
            rate_per_hour = 1.0
            span_hours = 24.0
            acceleration_factor = 1.0
            if len(messages) >= 2:
                try:
                    t_first = datetime.strptime(messages[0]["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    t_last = datetime.strptime(messages[-1]["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    diff_sec = (t_first - t_last).total_seconds()
                    span_hours = max(0.05, diff_sec / 3600.0)
                    rate_per_hour = round(len(messages) / span_hours, 1)

                    if len(messages) >= 8:
                        k = max(3, len(messages) // 3)
                        t_k = datetime.strptime(messages[k]["created_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        span_recent = max(0.01, (t_first - t_k).total_seconds() / 3600.0)
                        rate_recent = k / span_recent
                        span_baseline = max(0.02, (t_k - t_last).total_seconds() / 3600.0)
                        rate_baseline = (len(messages) - k) / span_baseline
                        acceleration_factor = round(rate_recent / max(0.05, rate_baseline), 2)
                except Exception as e:
                    logger.debug(f"Timestamp parse error for {ticker}: {e}")

            # Bayesian Laplace smoothing against a 50% neutral prior
            total_signals = bulls + bears
            bull_pct = round(((bulls + 2.0) / (total_signals + 4.0)) * 100.0, 1)

            return {
                "success": True,
                "total_messages": len(messages),
                "bulls": bulls,
                "bears": bears,
                "bull_pct": bull_pct,
                "rate_per_hour": rate_per_hour,
                "span_hours": round(span_hours, 2),
                "acceleration_factor": acceleration_factor,
                "sample_comments": sample_comments
            }
    except Exception as e:
        logger.debug(f"StockTwits fetch failed for {ticker}: {e}")

    return {
        "success": False,
        "total_messages": 0,
        "bulls": 0.0,
        "bears": 0.0,
        "bull_pct": 50.0,
        "rate_per_hour": 0.0,
        "span_hours": 24.0,
        "acceleration_factor": 1.0,
        "sample_comments": []
    }


def _fetch_reddit_discussion(ticker: str) -> Dict[str, Any]:
    """
    Fetches public Reddit search results across r/wallstreetbets and r/stocks.
    Uses Google News RSS index to bypass Reddit HTTP 429 rate limit blocks reliably.
    """
    clean_ticker = ticker.strip().upper()
    all_titles = []
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    # 1. Query Google News RSS Index for r/wallstreetbets discussions
    try:
        url = f"https://news.google.com/rss/search?q=site:reddit.com/r/wallstreetbets+{clean_ticker}&hl=en-US&gl=US&ceid=US:en"
        with httpx.Client(timeout=2.0, headers=browser_headers, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                for entry in feed.entries:
                    raw_title = html.unescape(entry.get("title", "")).replace(" - Reddit", "").strip()
                    if raw_title and "r/wallstreetbets" not in raw_title.lower() and len(raw_title) > 10:
                        if raw_title not in all_titles:
                            all_titles.append(raw_title)
                    if len(all_titles) >= 5:
                        break
    except Exception as e:
        logger.debug(f"Google Reddit RSS search failed for {clean_ticker}: {e}")

    # 2. Fallback to r/stocks if r/wallstreetbets yielded few items
    if len(all_titles) < 2:
        try:
            url_stocks = f"https://news.google.com/rss/search?q=site:reddit.com/r/stocks+{clean_ticker}&hl=en-US&gl=US&ceid=US:en"
            with httpx.Client(timeout=2.0, headers=browser_headers, follow_redirects=True) as client:
                resp = client.get(url_stocks)
                if resp.status_code == 200:
                    feed = feedparser.parse(resp.text)
                    for entry in feed.entries:
                        raw_title = html.unescape(entry.get("title", "")).replace(" - Reddit", "").strip()
                        if raw_title and "r/stocks" not in raw_title.lower() and len(raw_title) > 10:
                            if raw_title not in all_titles:
                                all_titles.append(raw_title)
                        if len(all_titles) >= 5:
                            break
        except Exception:
            pass

    return {
        "count": len(all_titles),
        "sample_titles": all_titles[:3]
    }


def fetch_social_sentiment_snapshot(
    ticker: str,
    live_volume: Optional[int] = None,
    avg_volume_20: Optional[int] = None,
    rvol: Optional[float] = None
) -> SentimentSnapshot:
    """
    Computes a grounded retail social sentiment snapshot combining StockTwits and Reddit.
    Automatically resolves RVOL from historical daily bars if not passed by caller.
    """
    clean_ticker = ticker.strip().upper()

    # 1. Query StockTwits
    st_data = _fetch_stocktwits_stream(clean_ticker)
    
    # 2. Query Reddit
    rd_data = _fetch_reddit_discussion(clean_ticker)

    # 3. Compute or Resolve RVOL (Relative Volume)
    computed_rvol = 1.0
    if rvol is not None and rvol > 0:
        computed_rvol = round(float(rvol), 2)
    elif live_volume and avg_volume_20 and avg_volume_20 > 0:
        computed_rvol = round(float(live_volume) / float(avg_volume_20), 2)
    else:
        # Automatically resolve from historical trading bars
        try:
            from analytics.technical_indicators import _fetch_historical_bars
            h_bars = _fetch_historical_bars(clean_ticker)
            valid_vols = [b["volume"] for b in h_bars if b.get("volume", 0) > 0]
            if len(valid_vols) >= 20:
                last_vol = valid_vols[-1]
                avg_vol = sum(valid_vols[-21:-1]) / 20.0
                if avg_vol > 0:
                    computed_rvol = round(float(last_vol) / float(avg_vol), 2)
        except Exception as e:
            logger.debug(f"Auto RVOL calculation error for {clean_ticker}: {e}")

    total_msgs = st_data["total_messages"]
    reddit_count = rd_data["count"]
    rate_per_hour = st_data.get("rate_per_hour", 0.0)
    accel = st_data.get("acceleration_factor", 1.0)
    span_hours = st_data.get("span_hours", 24.0)

    # If both sources failed
    if not st_data["success"] and reddit_count == 0:
        return SentimentSnapshot(
            ticker=clean_ticker,
            retail_bull_pct=50.0,
            retail_bear_pct=50.0,
            total_messages_analyzed=0,
            social_velocity="STEADY",
            reddit_post_count=0,
            messages_per_hour=0.0,
            acceleration_factor=1.0,
            span_hours=24.0,
            relative_volume=computed_rvol,
            composite_sentiment_score=50.0,
            sentiment_verdict="NEUTRAL_BALANCED",
            is_live=False,
            error="Social sentiment stream unavailable"
        )

    # Calculate True Social Velocity based on intra-stream acceleration & arrival rate
    if accel >= 2.0 or (rate_per_hour >= 25.0 and accel >= 1.2):
        social_velocity = "SURGING"
    elif accel >= 1.35 or (rate_per_hour >= 15.0 and accel >= 1.0):
        social_velocity = "ELEVATED"
    elif accel <= 0.65 and total_msgs >= 10:
        social_velocity = "COOLING"
    elif rate_per_hour < 0.2 and span_hours > 72.0:
        social_velocity = "DORMANT"
    else:
        social_velocity = "STEADY"

    bull_pct = st_data["bull_pct"]
    bear_pct = round(100.0 - bull_pct, 1)

    # Composite Sentiment Score (0 to 100) combining retail crowd posture and institutional volume confirmation
    composite = bull_pct
    if computed_rvol >= 1.3 and bull_pct >= 62.0:
        composite = min(100.0, composite + 6.0)  # Institutional volume confirming retail enthusiasm
    elif computed_rvol < 0.85 and bull_pct >= 75.0:
        composite = max(45.0, composite - 12.0) # Divergence: retail euphoria unsupported by volume (trap risk)
    elif computed_rvol >= 1.3 and bull_pct <= 38.0:
        composite = max(0.0, composite - 6.0)  # Institutional heavy volume confirming sell-off

    if composite >= 82.0:
        sentiment_verdict = "EXTREME_EUPHORIA"
    elif composite >= 62.0:
        sentiment_verdict = "BULLISH_ACCUMULATION"
    elif composite <= 25.0:
        sentiment_verdict = "EXTREME_CAPITULATION"
    elif composite <= 40.0:
        sentiment_verdict = "BEARISH_DISTRUST"
    elif computed_rvol < 0.85 and bull_pct >= 72.0:
        sentiment_verdict = "RETAIL_DIVERGENCE_TRAP"
    else:
        sentiment_verdict = "NEUTRAL_BALANCED"

    return SentimentSnapshot(
        ticker=clean_ticker,
        retail_bull_pct=bull_pct,
        retail_bear_pct=bear_pct,
        total_messages_analyzed=total_msgs,
        social_velocity=social_velocity,
        reddit_post_count=reddit_count,
        messages_per_hour=rate_per_hour,
        acceleration_factor=accel,
        span_hours=span_hours,
        sample_reddit_headlines=rd_data["sample_titles"],
        sample_stocktwits_comments=st_data["sample_comments"],
        relative_volume=computed_rvol,
        composite_sentiment_score=round(composite, 1),
        sentiment_verdict=sentiment_verdict,
        is_live=True
    )
