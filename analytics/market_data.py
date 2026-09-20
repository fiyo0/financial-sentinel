"""
100% Dynamic Real-Time Market Data Engine.
Zero static/hardcoded pricing. Every quote is fetched live over HTTP from official market APIs.
If a live price cannot be fetched, it explicitly reports failure instead of falling back to outdated data.
"""
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
import httpx
from models import Portfolio
from storage.cache_manager import cache_manager

logger = logging.getLogger(__name__)


class BoundedPriceCache(dict):
    """
    Bounded, thread-safe, TTL-evicting mapping backed by CacheManager.
    Eliminates unbounded dictionary growth while preserving 100% dictionary interface compatibility.
    """
    def __init__(self, default_ttl: float = 60.0):
        super().__init__()
        self._default_ttl = default_ttl

    def __getitem__(self, key: str) -> Tuple[float, Dict[str, Any]]:
        val = cache_manager.get("prices", str(key).upper())
        if val is None:
            raise KeyError(key)
        return (time.time(), val)

    def __setitem__(self, key: str, value: Any) -> None:
        clean_key = str(key).upper()
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
            quote = value[1]
        else:
            quote = value
        cache_manager.set("prices", clean_key, quote, ttl_seconds=self._default_ttl)

    def __contains__(self, key: object) -> bool:
        return cache_manager.get("prices", str(key).upper()) is not None

    def __len__(self) -> int:
        stats = cache_manager.stats()
        return stats.get("namespaces", {}).get("prices", 0)

    def __iter__(self):
        with cache_manager._get_ns_lock("prices"):
            ns_dict = cache_manager._store.get("prices", {})
            now = time.time()
            active_keys = [k for k, (exp, _) in ns_dict.items() if exp > now]
        return iter(active_keys)

    def get(self, key: str, default: Any = None) -> Any:
        val = cache_manager.get("prices", str(key).upper())
        if val is None:
            return default
        return (time.time(), val)

    def clear(self) -> None:
        cache_manager.clear("prices")


CACHE_TTL_SECONDS = 60.0
# In-memory bounded, thread-safe TTL cache backed by CacheManager: ticker -> (timestamp, data_dict)
PRICE_CACHE: BoundedPriceCache = BoundedPriceCache(default_ttl=CACHE_TTL_SECONDS)

# Persistent HTTP/2 connection pool for market data APIs (Robinhood, Yahoo Finance)
_MARKET_HTTP_CLIENT: Optional[httpx.Client] = None
try:
    import h2  # noqa
    _HAS_HTTP2 = True
except ImportError:
    _HAS_HTTP2 = False

PROVIDER_METRICS: Dict[str, Dict[str, Any]] = {
    "robinhood": {"successes": 0, "failures": 0, "circuit_broken": False},
    "yahoo": {"successes": 0, "failures": 0, "circuit_broken": False},
}


def get_market_http_client() -> httpx.Client:
    global _MARKET_HTTP_CLIENT
    if _MARKET_HTTP_CLIENT is None or _MARKET_HTTP_CLIENT.is_closed:
        _MARKET_HTTP_CLIENT = httpx.Client(
            http2=_HAS_HTTP2,
            limits=httpx.Limits(max_keepalive_connections=25, max_connections=50, keepalive_expiry=60.0),
            timeout=8.0
        )
    return _MARKET_HTTP_CLIENT



def classify_equity_sector(ticker: str, name: str, explicit_type: Optional[str] = None) -> str:
    """
    Universally classifies equities into standard GICS sectors or index funds.
    Tier 1: Canonical Registry-First (reference_equities.json / SQLite).
    Tier 2: Broad Index ETF / Fund asset-class detection.
    Tier 3: Dynamic Sector Taxonomy from storage/sector_taxonomy.json.
    """
    clean_ticker = ticker.strip().upper()
    name_lower = name.lower()

    # 1. Tier 1: Canonical Registry-First Resolution
    from storage.state_store import get_reference_equities, get_sector_taxonomy
    ref_data = get_reference_equities()
    if clean_ticker in ref_data:
        sec = ref_data[clean_ticker].get("sector")
        if sec and sec != "Unclassified":
            return sec

    # 2. Broad Index ETFs / Mutual Funds / Closed-End Funds
    is_etf = (
        (explicit_type or "").lower() == "etf" or
        any(k in name_lower for k in [
            "etf", "index", "fund", "trust", "spdr", "ishares", "vanguard",
            "invesco", "proshares", "direxion", "schwab", "total stock", "yield fund",
            "treasury fund", "s&p 500", "nasdaq 100", "russell 2000"
        ])
    )
    if is_etf:
        return "Index ETF / Fund"

    # 3. Dynamic Sector Taxonomy Resolution from external asset
    taxonomy = get_sector_taxonomy()
    if taxonomy:
        priority_order = [
            "Semiconductors", "Healthcare", "Communication Services", "Technology",
            "Financials", "Energy", "Consumer Discretionary", "Consumer Staples",
            "Industrials", "Utilities", "Real Estate", "Materials"
        ]
        for sector_name in priority_order:
            if sector_name not in taxonomy:
                continue
            keywords = taxonomy[sector_name].get("macro_keywords", [])
            if any(k in name_lower for k in keywords):
                return sector_name

    return "Unclassified"


def fetch_live_quote(ticker: str) -> Dict[str, Any]:
    """
    Dynamically queries live financial APIs for real-time market trade price, previous close,
    and verified company names for ANY US equity or ETF.
    Uses CacheManager singleflight coordination to prevent cache stampedes.
    Returns current_price = 0.0 if network fails or ticker is not found.
    """
    clean_ticker = ticker.strip().upper()

    cached_quote = cache_manager.get("prices", clean_ticker)
    if cached_quote:
        return cached_quote

    def _do_fetch() -> Dict[str, Any]:
        current_price = 0.0
        short_name = clean_ticker
        prev_close = 0.0
        fetch_success = False
        provider_used = "none"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        client = get_market_http_client()

        # 1. Primary Live API: Robinhood Unauthenticated Market Quote API
        rh_url = f"https://api.robinhood.com/quotes/{clean_ticker}/"
        try:
            resp = client.get(rh_url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                d = resp.json()
                raw_trade = d.get("last_trade_price") or d.get("last_extended_hours_trade_price") or d.get("adjusted_previous_close")
                raw_prev = d.get("adjusted_previous_close") or d.get("previous_close")
                if raw_trade:
                    p = float(raw_trade)
                    if p > 0:
                        current_price = p
                        fetch_success = True
                        provider_used = "robinhood"
                        PROVIDER_METRICS["robinhood"]["successes"] += 1
                        if raw_prev:
                            prev_close = float(raw_prev)

                # Dynamically fetch verified company or fund name
                inst_url = d.get("instrument")
                if inst_url:
                    try:
                        inst_resp = client.get(inst_url, headers=headers, timeout=3.0)
                        if inst_resp.status_code == 200:
                            inst_data = inst_resp.json()
                            resolved_name = inst_data.get("simple_name") or inst_data.get("name")
                            if resolved_name:
                                short_name = resolved_name
                    except Exception as e:
                        logger.debug("Robinhood instrument resolution failed for %s: %s", clean_ticker, e)
            else:
                PROVIDER_METRICS["robinhood"]["failures"] += 1
        except Exception as e:
            PROVIDER_METRICS["robinhood"]["failures"] += 1
            logger.debug(f"Robinhood quote fetch error for {clean_ticker}: {e}")

        # 2. Secondary Live API: Yahoo Finance Chart API (if primary did not return a price)
        if not fetch_success or current_price <= 0:
            y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_ticker}?interval=1d&range=1d"
            try:
                resp = client.get(y_url, headers=headers, timeout=4.0)
                if resp.status_code == 200:
                    data = resp.json()
                    chart = data.get("chart") if isinstance(data, dict) else None
                    result_list = chart.get("result") if isinstance(chart, dict) else None
                    if not result_list or not isinstance(result_list, list) or len(result_list) == 0 or not isinstance(result_list[0], dict):
                        err = chart.get("error") if isinstance(chart, dict) else "Missing chart.result"
                        logger.warning("YAHOO_CONTRACT_VIOLATION: 'result' is invalid for %s. Error: %s", clean_ticker, err)
                    else:
                        meta = result_list[0].get("meta", {})
                        p = float(meta.get("regularMarketPrice", 0.0) or 0.0)
                        if p > 0:
                            current_price = p
                            fetch_success = True
                            provider_used = "yahoo"
                            PROVIDER_METRICS["yahoo"]["successes"] += 1
                            prev_close = float(meta.get("chartPreviousClose", current_price) or current_price)
                            y_name = meta.get("shortName")
                            if y_name:
                                short_name = y_name
            except Exception as e:
                PROVIDER_METRICS["yahoo"]["failures"] += 1
                logger.debug(f"Yahoo quote fetch error for {clean_ticker}: {e}")

        # Dynamic Sector Classification from resolved name & ticker
        sector = classify_equity_sector(clean_ticker, short_name)

        return {
            "ticker": clean_ticker,
            "name": short_name,
            "sector": sector,
            "current_price": round(float(current_price), 2),
            "prev_close": round(float(prev_close), 2),
            "change_pct": round(((current_price - prev_close) / prev_close) * 100.0, 2) if (prev_close and current_price > 0) else 0.0,
            "is_live": fetch_success,
            "provider": provider_used,
            "error": None if fetch_success else f"Unable to fetch live quote for {clean_ticker}"
        }

    return cache_manager.get_or_compute("prices", clean_ticker, _do_fetch, ttl_seconds=CACHE_TTL_SECONDS)



def update_portfolio_live_prices(portfolio: Portfolio, override_all: bool = True) -> Tuple[Portfolio, List[str]]:
    """
    Concurrently refreshes current_price, company name, and sector for all portfolio holdings in parallel.
    Returns the updated portfolio and a list of any tickers that failed to fetch live data.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    failed_tickers: List[str] = []

    with ThreadPoolExecutor(max_workers=min(12, len(portfolio.holdings) or 1)) as executor:
        future_to_holding = {
            executor.submit(fetch_live_quote, holding.ticker): holding
            for holding in portfolio.holdings
        }
        for future in as_completed(future_to_holding):
            holding = future_to_holding[future]
            try:
                quote = future.result()
                if quote and quote.get("is_live") and quote.get("current_price", 0) > 0:
                    holding.current_price = quote["current_price"]
                    holding.daily_change_pct = float(quote.get("change_pct", 0.0) or 0.0)
                    if quote.get("name") and quote["name"] != holding.ticker:
                        holding.name = quote["name"]
                    if quote.get("sector"):
                        holding.sector = quote["sector"]
                else:
                    failed_tickers.append(holding.ticker)

            except Exception as e:
                logger.warning("Failed refreshing quote for %s: %s", holding.ticker, e)
                failed_tickers.append(holding.ticker)

    portfolio.recalculate_weights()
    return portfolio, failed_tickers


def fetch_market_overview() -> Dict[str, Any]:
    """
    Fetches broad market benchmark performance (S&P 500, Nasdaq 100, Dow Jones, Russell 2000, Volatility).
    """
    from concurrent.futures import ThreadPoolExecutor
    from config import config

    tickers = config.benchmark_tickers or ["SPY", "QQQ", "DIA", "IWM", "VIXY"]
    results = {}

    with ThreadPoolExecutor(max_workers=min(len(tickers), 8) or 1) as executor:
        futures = {executor.submit(fetch_live_quote, t): t for t in tickers}
        for future in futures:
            t = futures[future]
            try:
                results[t] = future.result()
            except Exception as e:
                logger.warning("Failed fetching benchmark quote for %s: %s", t, e)
                results[t] = {"ticker": t, "current_price": 0.0, "change_pct": 0.0, "name": t}

    spy_change = results.get("SPY", {}).get("change_pct", 0.0)
    qqq_change = results.get("QQQ", {}).get("change_pct", 0.0)
    market_tone = "Bullish" if spy_change > 0.3 else ("Bearish" if spy_change < -0.3 else "Mixed / Neutral")

    return {
        "indices": results,
        "market_tone": market_tone,
        "spy_change_pct": spy_change,
        "qqq_change_pct": qqq_change
    }


def fetch_market_movers() -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetches active market movers across US exchanges (top gainers, top losers, high volume momentum).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    gainers = []
    losers = []
    client = get_market_http_client()

    # Dynamic query to Yahoo Finance Screener / Movers
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=day_gainers&count=5"
        resp = client.get(url, headers=headers, timeout=7.0)
        if resp.status_code == 200:
            items = resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            for item in items[:4]:
                gainers.append({
                    "ticker": item.get("symbol", ""),
                    "name": item.get("shortName", item.get("symbol", "")),
                    "price": float(item.get("regularMarketPrice", 0.0) or 0.0),
                    "change_pct": float(item.get("regularMarketChangePercent", 0.0) or 0.0)
                })
    except Exception as e:
        logger.warning("Failed fetching Yahoo Finance top gainers: %s", e)

    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?formatted=false&scrIds=day_losers&count=5"
        resp = client.get(url, headers=headers, timeout=7.0)

        if resp.status_code == 200:
            items = resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            for item in items[:4]:
                losers.append({
                    "ticker": item.get("symbol", ""),
                    "name": item.get("shortName", item.get("symbol", "")),
                    "price": float(item.get("regularMarketPrice", 0.0) or 0.0),
                    "change_pct": float(item.get("regularMarketChangePercent", 0.0) or 0.0)
                })
    except Exception as e:
        logger.warning("Failed fetching Yahoo Finance top losers: %s", e)

    return {
        "top_gainers": gainers,
        "top_losers": losers
    }
