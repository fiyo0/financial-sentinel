"""
Multi-Source Social & Retail Sentiment Ingestion Stream.
Combines StockTwits public symbol stream (with true time velocity and keyword-augmented sentiment)
and Reddit (r/wallstreetbets, r/stocks via Google News RSS index) with Relative Trading Volume (RVOL)
to compute mathematically and empirically grounded retail sentiment velocity.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from math import exp
import hashlib
import re
import html
import json
import logging
import httpx
import feedparser
from storage.cache_manager import cache_manager

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

BULL_KW = re.compile(r"\b(buy|bought|calls|call\s+option|moon|breakout|rocket|undervalued|rally|accumulate|buy\s+the\s+dip|bullish|bouncing\s+off\s+support)\b", re.I)
BEAR_KW = re.compile(r"\b(sell|sold|puts|put\s+option|shorting|overvalued|crash|scam|downgrade|bubble|bearish|tanking|dumping|selloff|sell-off|bagholding)\b", re.I)


def classify_comments_sentiment(
    texts: List[str],
    api_key: Optional[str] = None
) -> List[str]:
    """
    Classifies sentiment for untagged social commentary.
    Tier 1: High-accuracy structured Gemini 2.5 Flash batch classification with 15-minute caching.
    Tier 2: Algorithmic keyword regex fallback (BULL_KW / BEAR_KW).
    Returns list of 'BULLISH', 'BEARISH', or 'NEUTRAL' for each text.
    """
    if not texts:
        return []

    cache_hash = hashlib.sha256("||".join(texts).encode("utf-8")).hexdigest()[:16]
    cached = cache_manager.get("social_sentiment", f"batch:{cache_hash}")
    if cached is not None and isinstance(cached, list) and len(cached) == len(texts):
        return cached

    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            prompt = (
                "You are an institutional financial sentiment analyst. "
                "Classify the sentiment of each of the following retail trader comments into exactly one of: "
                "BULLISH, BEARISH, or NEUTRAL. Identify sarcasm, bagholder distress, or cynical humor accurately. "
                "Return a JSON list of strings matching the input order.\n"
                f"Comments:\n{json.dumps(texts[:30])}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1
                }
            }
            with httpx.Client(timeout=6.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content = candidates[0].get("content", {})
                        parts = content.get("parts", [])
                        if parts:
                            raw_text = parts[0].get("text", "")
                            labels = json.loads(raw_text)
                            if isinstance(labels, list) and len(labels) == len(texts[:30]):
                                res = [str(lbl).upper() for lbl in labels]
                                cache_manager.set("social_sentiment", f"batch:{cache_hash}", res, ttl_seconds=900.0)
                                return res
        except Exception as e:
            logger.debug("Gemini 2.5 Flash sentiment batch classification unavailable (%s); falling back to keyword regex.", e)

    # Tier 2 Fallback: Regex Keyword Classification
    results = []
    for body in texts:
        has_bull = bool(BULL_KW.search(body))
        has_bear = bool(BEAR_KW.search(body))
        if has_bull and not has_bear:
            results.append("BULLISH")
        elif has_bear and not has_bull:
            results.append("BEARISH")
        else:
            results.append("NEUTRAL")
    return results


@dataclass
class SentimentSnapshot:
    ticker: str
    retail_bull_pct: float
    retail_bear_pct: float
    total_messages_analyzed: int
    social_velocity: str  # SURGING, ELEVATED, STEADY, COOLING, DORMANT, DORMANT_APATHY
    reddit_post_count: int
    messages_per_hour: float = 0.0
    acceleration_factor: float = 1.0
    span_hours: float = 24.0
    sample_reddit_headlines: List[str] = field(default_factory=list)
    sample_stocktwits_comments: List[str] = field(default_factory=list)
    relative_volume: float = 1.0  # RVOL (1.0 = normal)
    composite_sentiment_score: float = 50.0  # 0 to 100
    sentiment_verdict: str = "NEUTRAL_BASELINE"
    is_live: bool = True
    error: Optional[str] = None
    is_sample_significant: bool = True
    display_label: str = ""
    contrarian_signal: str = ""

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
        if not self.is_sample_significant or self.social_velocity == "DORMANT_APATHY":
            return f"Social: Dormant / Apathy ({self.total_messages_analyzed} in {self.format_recency_window()}) | Signal: Institutional Domain | RVOL: {self.relative_volume:.2f}x"
        recency = self.format_recency_window()
        rate_str = f" ({self.messages_per_hour:.1f}/hr · {self.total_messages_analyzed} in {recency} · {self.acceleration_factor:.1f}x)" if (self.messages_per_hour > 0 or self.total_messages_analyzed > 0) else ""
        label_str = self.display_label or f"{self.retail_bull_pct:.0f}% Bullish"
        return (
            f"Social: {label_str} ({self.sentiment_verdict.replace('_', ' ')}) | "
            f"Velocity: {self.social_velocity}{rate_str} | RVOL: {self.relative_volume:.2f}x"
        )

    def to_telegram_block(self) -> str:
        if not self.is_live:
            return "💬 <i>Social sentiment stream currently unavailable.</i>"

        recency = self.format_recency_window()
        if not self.is_sample_significant or self.social_velocity == "DORMANT_APATHY":
            contrarian_line = f"\n• <b>Contrarian Signal:</b> 🏛️ <code>{self.contrarian_signal}</code>" if self.contrarian_signal else ""
            return (
                f"💬 <b>Retail Social Sentiment & Velocity:</b>\n"
                f"• <b>Crowd Sentiment:</b> ⚪ <code>DORMANT / APATHY</code> (Insufficient retail presence; institutional domain)\n"
                f"• <b>Activity Velocity:</b> <code>DORMANT</code> ({self.total_messages_analyzed} posts in {recency} on StockTwits)\n"
                f"• <b>Relative Trading Volume (RVOL):</b> <code>{self.relative_volume:.2f}x</code>"
                f"{contrarian_line}"
            )

        emoji = "🟢" if self.retail_bull_pct >= 58.0 else ("🔴" if self.retail_bull_pct <= 45.0 else "⚪")
        label_str = self.display_label or f"{self.retail_bull_pct:.0f}% Bullish"
        accel_str = f" · {self.acceleration_factor:.1f}x accel" if self.acceleration_factor > 0 else ""
        velocity_detail = f"{self.messages_per_hour:.1f} msgs/hr · {self.total_messages_analyzed} in {recency}{accel_str}" if self.messages_per_hour > 0 else f"{self.total_messages_analyzed} posts"
        reddit_detail = f", {self.reddit_post_count} Reddit threads" if self.reddit_post_count > 0 else ""
        contrarian_line = f"\n• <b>Contrarian Signal:</b> <code>{self.contrarian_signal}</code>" if self.contrarian_signal else ""

        return (
            f"💬 <b>Retail Social Sentiment & Velocity:</b>\n"
            f"• <b>Crowd Sentiment:</b> {emoji} <code>{label_str}</code> "
            f"({self.sentiment_verdict.replace('_', ' ')})\n"
            f"• <b>Activity Velocity:</b> <code>{self.social_velocity}</code> "
            f"({velocity_detail} on StockTwits{reddit_detail})\n"
            f"• <b>Relative Trading Volume (RVOL):</b> <code>{self.relative_volume:.2f}x</code>"
            f"{contrarian_line}"
        )


def _fetch_stocktwits_stream(
    ticker: str,
    api_key: Optional[str] = None,
    reference_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Fetches real-time retail messages from StockTwits public symbol stream.
    Features:
    - Adaptive cursor pagination: if initial 30 messages span < 2 hours (high velocity), fetches up to 90 messages.
    - 72-hour horizon filter: discards stale messages older than 72 hours.
    - Exponential recency time-decay weighting (half-life ~ 24h).
    - Statistical significance gate: marks is_sample_significant=False if valid messages < 8.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    headers = {"User-Agent": STOCKTWITS_UA}
    try:
        client = get_stocktwits_client()
        resp = client.get(url, headers=headers, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            all_messages = list(data.get("messages", []))
            cursor_max = data.get("cursor", {}).get("max")

            # Adaptive pagination for high-velocity tickers
            if len(all_messages) >= 30 and cursor_max:
                p1_dates = []
                for m in all_messages:
                    ts_raw = str(m.get("created_at", ""))[:19]
                    if ts_raw:
                        try:
                            p1_dates.append(datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc))
                        except (ValueError, TypeError):
                            pass
                if len(p1_dates) >= 2:
                    p1_dates.sort(reverse=True)
                    p1_span_hours = max(0.01, (p1_dates[0] - p1_dates[-1]).total_seconds() / 3600.0)
                    if p1_span_hours < 2.0:
                        try:
                            p2_resp = client.get(f"{url}?max={cursor_max}", headers=headers, timeout=3.0)
                            if p2_resp.status_code == 200:
                                p2_data = p2_resp.json()
                                p2_msgs = p2_data.get("messages", [])
                                existing_ids = {m.get("id") for m in all_messages if m.get("id")}
                                for m in p2_msgs:
                                    if m.get("id") not in existing_ids:
                                        all_messages.append(m)
                                        existing_ids.add(m.get("id"))
                                p2_max = p2_data.get("cursor", {}).get("max")
                                if len(all_messages) < 90 and p2_max:
                                    p3_resp = client.get(f"{url}?max={p2_max}", headers=headers, timeout=3.0)
                                    if p3_resp.status_code == 200:
                                        for m in p3_resp.json().get("messages", []):
                                            if m.get("id") not in existing_ids:
                                                all_messages.append(m)
                                                existing_ids.add(m.get("id"))
                        except Exception as e:
                            logger.debug("StockTwits pagination failed for %s: %s", ticker, e)

            # Parse timestamps and sort chronologically (newest first)
            parsed_msgs = []
            for m in all_messages:
                ts_str = str(m.get("created_at", ""))[:19]
                dt = None
                if ts_str:
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError) as e:
                        logger.debug("Failed parsing message timestamp '%s': %s", ts_str, e)
                parsed_msgs.append((dt, m))

            parsed_msgs.sort(key=lambda x: (x[0] is not None, x[0]), reverse=True)

            now = datetime.now(timezone.utc)
            ref_time = reference_time or now
            if parsed_msgs and parsed_msgs[0][0] is not None:
                t_newest = parsed_msgs[0][0]
                if reference_time is None and (now - t_newest).total_seconds() > 30 * 86400:
                    ref_time = t_newest

            # Filter messages within strict 72-hour horizon
            valid_msgs = []
            for dt, m in parsed_msgs:
                if dt is None:
                    valid_msgs.append((dt, m, 0.0))
                else:
                    age_hours = max(0.0, (ref_time - dt).total_seconds() / 3600.0)
                    if age_hours <= 72.0:
                        valid_msgs.append((dt, m, age_hours))

            is_sample_significant = len(valid_msgs) >= 8

            sample_comments = []
            untagged_items = []
            bulls = 0.0
            bears = 0.0

            for _dt, m, age_hours in valid_msgs:
                body = m.get("body", "")
                entities = m.get("entities") or {}
                sent_obj = entities.get("sentiment")

                w = exp(-age_hours / 24.0)

                if isinstance(sent_obj, dict):
                    basic = sent_obj.get("basic")
                    if basic == "Bullish":
                        bulls += 1.0 * w
                    elif basic == "Bearish":
                        bears += 1.0 * w
                elif body:
                    untagged_items.append((body, w))

                if body and len(sample_comments) < 3 and len(body) > 20:
                    clean_body = re.sub(r'https?://\S+', '', body).strip()
                    if clean_body and ("$" in clean_body or len(clean_body) > 30):
                        sample_comments.append(clean_body[:100])

            if untagged_items:
                untagged_bodies = [item[0] for item in untagged_items]
                classified_sentiments = classify_comments_sentiment(untagged_bodies, api_key=api_key)
                for i, sent_lbl in enumerate(classified_sentiments):
                    w = untagged_items[i][1]
                    if sent_lbl == "BULLISH":
                        bulls += 0.5 * w
                    elif sent_lbl == "BEARISH":
                        bears += 0.5 * w

            rate_per_hour = 1.0
            span_hours = 24.0
            acceleration_factor = 1.0
            timed_msgs = [vm for vm in valid_msgs if vm[0] is not None]
            if len(timed_msgs) >= 2:
                t_newest = timed_msgs[0][0]
                t_oldest = timed_msgs[-1][0]
                diff_sec = max(1.0, (t_newest - t_oldest).total_seconds())
                span_hours = max(0.05, diff_sec / 3600.0)
                rate_per_hour = round(len(timed_msgs) / span_hours, 1)

                if len(timed_msgs) >= 8:
                    k = max(3, len(timed_msgs) // 3)
                    t_k = timed_msgs[k][0]
                    span_recent = max(0.01, (t_newest - t_k).total_seconds() / 3600.0)
                    rate_recent = k / span_recent
                    span_baseline = max(0.02, (t_k - t_oldest).total_seconds() / 3600.0)
                    rate_baseline = (len(timed_msgs) - k) / span_baseline
                    acceleration_factor = round(rate_recent / max(0.05, rate_baseline), 2)

            total_signals = bulls + bears
            if total_signals > 0:
                bull_pct = round(((bulls + 2.0) / (total_signals + 4.0)) * 100.0, 1)
            else:
                bull_pct = 50.0

            return {
                "success": True,
                "total_messages": len(valid_msgs),
                "is_sample_significant": is_sample_significant,
                "bulls": round(bulls, 2),
                "bears": round(bears, 2),
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
        "is_sample_significant": False,
        "bulls": 0.0,
        "bears": 0.0,
        "bull_pct": 50.0,
        "rate_per_hour": 0.0,
        "span_hours": 24.0,
        "acceleration_factor": 1.0,
        "sample_comments": []
    }


def _fetch_reddit_discussion(
    ticker: str,
    aliases: Optional[List[str]] = None,
    company_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetches public Reddit search results across r/wallstreetbets and r/stocks.
    Uses equity-anchored search syntax (cashtags and brand names) to eliminate
    dictionary-word false positives on tickers like HOOD, CAT, and ON.
    """
    clean_ticker = ticker.strip().upper()
    all_titles = []
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    # Resolve primary brand anchor
    primary_brand = None
    if aliases:
        for a in aliases:
            if a.upper() != clean_ticker and len(a) > 2:
                primary_brand = a
                break
    if not primary_brand and company_name and company_name.upper() != clean_ticker:
        primary_brand = company_name.split()[0]

    if primary_brand:
        search_kw = f'("{primary_brand}" OR "${clean_ticker}" OR "{clean_ticker} stock")'
    else:
        search_kw = f'("${clean_ticker}" OR "{clean_ticker} stock")'

    import urllib.parse
    encoded_kw = urllib.parse.quote(search_kw)

    # 1. Query Google News RSS Index for r/wallstreetbets discussions
    try:
        url = f"https://news.google.com/rss/search?q=site:reddit.com/r/wallstreetbets+{encoded_kw}&hl=en-US&gl=US&ceid=US:en"
        with httpx.Client(timeout=2.5, headers=browser_headers, follow_redirects=True) as client:
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
            url_stocks = f"https://news.google.com/rss/search?q=site:reddit.com/r/stocks+{encoded_kw}&hl=en-US&gl=US&ceid=US:en"
            with httpx.Client(timeout=2.5, headers=browser_headers, follow_redirects=True) as client:
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
        except Exception as e:
            logger.debug("Failed scraping secondary Reddit feed for %s: %s", clean_ticker, e)

    return {
        "count": len(all_titles),
        "sample_titles": all_titles[:3]
    }


def fetch_social_sentiment_snapshot(
    ticker: str,
    live_volume: Optional[int] = None,
    avg_volume_20: Optional[int] = None,
    rvol: Optional[float] = None,
    aliases: Optional[List[str]] = None,
    company_name: Optional[str] = None,
    api_key: Optional[str] = None
) -> SentimentSnapshot:
    """
    Computes a grounded retail social sentiment snapshot combining StockTwits and Reddit.
    Automatically resolves RVOL from historical daily bars if not passed by caller.
    """
    clean_ticker = ticker.strip().upper()

    # 1. Query StockTwits with adaptive cursor pagination & optional Gemini 2.5 Flash batch classification
    st_data = _fetch_stocktwits_stream(clean_ticker, api_key=api_key)

    # 2. Query Reddit with equity-anchored search terms
    rd_data = _fetch_reddit_discussion(clean_ticker, aliases=aliases, company_name=company_name)

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
            if len(valid_vols) >= 21:
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
    is_sample_significant = st_data.get("is_sample_significant", True)

    # If both sources failed completely
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
            sentiment_verdict="NEUTRAL_BASELINE",
            is_live=False,
            error="Social sentiment stream unavailable",
            is_sample_significant=False,
            display_label="Unavailable",
            contrarian_signal="Unavailable"
        )

    # Calculate True Social Velocity based on intra-stream acceleration & arrival rate
    if not is_sample_significant or total_msgs < 8:
        social_velocity = "DORMANT_APATHY"
    elif accel >= 2.0 or (rate_per_hour >= 25.0 and accel >= 1.2):
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
    if computed_rvol >= 1.3 and bull_pct >= 70.0:
        composite = min(100.0, composite + 6.0)  # Institutional volume confirming retail enthusiasm
    elif computed_rvol < 0.85 and bull_pct >= 72.0:
        composite = max(45.0, composite - 12.0) # Divergence: retail euphoria unsupported by volume (trap risk)
    elif computed_rvol >= 1.3 and bull_pct <= 45.0:
        composite = max(0.0, composite - 6.0)  # Institutional heavy volume confirming sell-off

    # Strata Ladder (Re-centered around 65% Empirical Baseline)
    if not is_sample_significant or total_msgs < 8:
        sentiment_verdict = "DORMANT_APATHY"
    elif computed_rvol < 0.85 and bull_pct >= 72.0:
        sentiment_verdict = "RETAIL_DIVERGENCE_TRAP"
    elif composite >= 82.0:
        sentiment_verdict = "EXTREME_EUPHORIA"
    elif composite >= 70.0:
        sentiment_verdict = "MODERATELY_BULLISH"
    elif composite >= 58.0:
        sentiment_verdict = "NEUTRAL_BASELINE"
    elif composite <= 25.0:
        sentiment_verdict = "EXTREME_CAPITULATION"
    else:
        sentiment_verdict = "BEARISH_DISTRUST"

    # Dynamic Polarity Label
    if not is_sample_significant or sentiment_verdict == "DORMANT_APATHY":
        display_label = "Dormant / Insufficient Posts"
    elif sentiment_verdict == "NEUTRAL_BASELINE":
        display_label = f"{bull_pct:.0f}% Neutral / Balanced"
    elif bull_pct >= 70.0 or sentiment_verdict in ("EXTREME_EUPHORIA", "MODERATELY_BULLISH"):
        display_label = f"{bull_pct:.0f}% Bullish"
    elif bull_pct <= 45.0 or sentiment_verdict in ("BEARISH_DISTRUST", "EXTREME_CAPITULATION"):
        display_label = f"{bear_pct:.0f}% Bearish"
    else:
        display_label = f"{bull_pct:.0f}% Neutral / Balanced"

    # Dual-Perspective Contrarian Signaling
    if not is_sample_significant or sentiment_verdict == "DORMANT_APATHY":
        contrarian_signal = "INSTITUTIONAL DOMAIN (Retail apathetic; price action driven by institutional cash flows)"
    elif sentiment_verdict == "RETAIL_DIVERGENCE_TRAP":
        contrarian_signal = "⚠️ EXHAUSTION WARNING (Retail euphoria unsupported by volume; distribution risk)"
    elif sentiment_verdict == "EXTREME_EUPHORIA":
        if computed_rvol >= 1.3:
            contrarian_signal = "🚀 INSTITUTIONAL MOMENTUM (High crowd conviction backed by strong volume breakout)"
        else:
            contrarian_signal = "⚠️ EXHAUSTION WARNING (Retail euphoria without volume confirmation; elevated pull-back risk)"
    elif sentiment_verdict == "MODERATELY_BULLISH":
        contrarian_signal = "📈 CONSTRUCTIVE PARTICIPATION (Healthy retail enthusiasm aligned with market trend)"
    elif sentiment_verdict == "NEUTRAL_BASELINE":
        contrarian_signal = "⚖️ BALANCED PARTICIPATION (Retail sentiment matches broad market baseline; fundamentals drive trend)"
    elif sentiment_verdict == "EXTREME_CAPITULATION":
        contrarian_signal = "⚡ CAPITULATION OPPORTUNITY (Extreme retail panic selling; asymmetric contrarian reversal potential)"
    else: # BEARISH_DISTRUST
        if computed_rvol >= 1.3:
            contrarian_signal = "⚡ LIQUIDATION PRESSURE (Active selling volume confirming retail skepticism)"
        else:
            contrarian_signal = "🛡️ WALL OF WORRY (Price climbing retail skepticism; short squeeze / support potential)"

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
        is_live=True,
        is_sample_significant=is_sample_significant,
        display_label=display_label,
        contrarian_signal=contrarian_signal
    )
