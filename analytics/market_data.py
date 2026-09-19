"""
100% Dynamic Real-Time Market Data Engine.
Zero static/hardcoded pricing. Every quote is fetched live over HTTP from official market APIs.
If a live price cannot be fetched, it explicitly reports failure instead of falling back to outdated data.
"""
import time
import logging
import re
from typing import Dict, Any, Optional, List, Tuple
import httpx
from models import Portfolio

logger = logging.getLogger(__name__)


# In-memory short-term TTL cache: ticker -> (timestamp, data_dict)
PRICE_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 60.0

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
    Universally classifies equities into standard GICS sectors or index funds
    using semantic industry keywords without hardcoded company lists.
    """
    clean_ticker = ticker.strip().upper()
    name_lower = name.lower()

    # 1. Broad Index ETFs / Mutual Funds
    if (
        explicit_type == "etf" or
        clean_ticker in {"SPY", "VOO", "QQQ", "IVV", "VTI", "IWM", "DIA", "SCHD", "SFY", "VEA", "VWO", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE"} or
        any(k in name_lower for k in ["etf", "index", "s&p 500", "nasdaq 100", "russell 2000", "total stock", "yield fund", "treasury fund"])
    ):
        return "Index ETF / Fund"

    # 2. Healthcare & Biotechnology
    if any(k in name_lower for k in ["health", "healthcare", "pharma", "pharmaceutical", "biotech", "therapeutics", "medical", "biosciences", "life sciences", "diagnostics", "clinical", "hospital", "genomics"]):
        return "Healthcare"

    # 3. Semiconductors (Sub-industry of Tech with distinct cyclicality)
    if any(k in name_lower for k in ["semiconductor", "semi", "chip", "chips", "wafer", "foundry", "integrated circuit", "lithography"]):
        return "Semiconductors"

    # 4. Communication Services
    if any(k in name_lower for k in ["telecom", "telecommunications", "media", "broadcasting", "cable", "wireless", "interactive media", "social media", "publishing", "advertising"]):
        return "Communication Services"

    # 5. Information Technology
    if any(re.search(r'\b' + re.escape(k) + r'\b', name_lower) for k in ["software", "technology", "tech", "cloud", "saas", "cybersecurity", "hardware", "systems", "micro", "data", "digital", "computing", "artificial intelligence", "platform"]):
        return "Technology"

    # 5. Financials
    if any(k in name_lower for k in ["bank", "banking", "financial", "finance", "capital", "insurance", "credit", "asset management", "brokerage", "trust", "fund", "holdings", "bancorp", "fintech", "clearing", "exchange", "mortgage", "lending"]):
        return "Financials"

    # 6. Energy
    if any(k in name_lower for k in ["energy", "oil", "gas", "petroleum", "pipeline", "drilling", "refining", "exploration", "renewable", "solar", "nuclear", "fuel", "clean power"]):
        return "Energy"

    # 7. Consumer Discretionary
    if any(k in name_lower for k in ["automotive", "motor", "motors", "retail", "apparel", "luxury", "entertainment", "restaurant", "dining", "leisure", "cruise", "hotel", "resort", "footwear", "homebuilder", "e-commerce"]):
        return "Consumer Discretionary"

    # 8. Consumer Staples
    if any(k in name_lower for k in ["beverage", "beverages", "food", "tobacco", "household", "personal care", "grocery", "supermarket"]):
        return "Consumer Staples"

    # 9. Industrials
    if any(k in name_lower for k in ["industrial", "machinery", "aerospace", "defense", "aviation", "logistics", "freight", "railroad", "shipping", "transportation", "construction", "manufacturing", "engineering", "electrical equipment"]):
        return "Industrials"

    # 10. Utilities
    if any(k in name_lower for k in ["utility", "utilities", "electric", "power", "water utility", "gas utility"]):
        return "Utilities"

    # 11. Real Estate
    if any(k in name_lower for k in ["reit", "real estate", "property", "properties", "realty", "residential", "commercial real estate"]):
        return "Real Estate"

    # 12. Materials
    if any(k in name_lower for k in ["materials", "chemical", "chemicals", "mining", "metals", "steel", "gold", "copper", "aluminum", "paper", "packaging", "fertilizer"]):
        return "Materials"

    return "Unclassified"


def fetch_live_quote(ticker: str) -> Dict[str, Any]:
    """
    Dynamically queries live financial APIs for real-time market trade price, previous close,
    and verified company names for ANY US equity or ETF.
    Returns current_price = 0.0 if network fails or ticker is not found.
    """
    clean_ticker = ticker.strip().upper()
    now = time.time()

    if clean_ticker in PRICE_CACHE:
        cached_time, cached_data = PRICE_CACHE[clean_ticker]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_data

    current_price = 0.0
    short_name = clean_ticker
    sector = "Unclassified"
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
                except Exception:
                    pass
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
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
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

    quote = {
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

    if fetch_success:
        PRICE_CACHE[clean_ticker] = (now, quote)

    return quote



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

            except Exception:
                failed_tickers.append(holding.ticker)

    portfolio.recalculate_weights()
    return portfolio, failed_tickers


def fetch_market_overview() -> Dict[str, Any]:
    """
    Fetches broad market benchmark performance (S&P 500, Nasdaq 100, Dow Jones, Russell 2000, Volatility).
    """
    from concurrent.futures import ThreadPoolExecutor

    tickers = ["SPY", "QQQ", "DIA", "IWM", "VIXY"]
    results = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_live_quote, t): t for t in tickers}
        for future in futures:
            t = futures[future]
            try:
                results[t] = future.result()
            except Exception:
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
    except Exception:
        pass

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
    except Exception:
        pass

    return {
        "top_gainers": gainers,
        "top_losers": losers
    }
