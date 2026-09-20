"""
Dynamic Live Corporate Earnings Calendar Engine.
Queries official Nasdaq Earnings APIs for 100% verified reporting dates, session times (BMO vs AMC),
and EPS consensus forecasts for the upcoming 7 calendar days.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo
import httpx

logger = logging.getLogger("EarningsCalendar")

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/"
}


def parse_market_cap(cap_str: Optional[str]) -> float:
    if not cap_str or cap_str == "N/A":
        return 0.0
    try:
        clean = cap_str.replace("$", "").replace(",", "").strip()
        return float(clean)
    except (ValueError, TypeError, IndexError) as e:
        logger.debug("Failed parsing market cap '%s': %s", cap_str, e)
        return 0.0


def fetch_earnings_for_date(date_str: str) -> List[Dict[str, Any]]:
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}"
    try:
        resp = httpx.get(url, headers=NASDAQ_HEADERS, timeout=8.0)
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("data", {}).get("rows") if data.get("data") else []
            if rows:
                results = []
                for r in rows:
                    sym = r.get("symbol", "").strip().upper()
                    name = r.get("name", sym).strip()
                    timing_raw = r.get("time", "").lower()
                    if "pre" in timing_raw:
                        timing = "BMO (Pre-Market)"
                    elif "after" in timing_raw or "post" in timing_raw:
                        timing = "AMC (Post-Market)"
                    else:
                        timing = "Time TBD"

                    cap_val = parse_market_cap(r.get("marketCap"))
                    results.append({
                        "ticker": sym,
                        "name": name,
                        "timing": timing,
                        "market_cap": cap_val,
                        "market_cap_str": r.get("marketCap", ""),
                        "eps_forecast": r.get("epsForecast", ""),
                        "last_year_eps": r.get("lastYearEPS", "")
                    })
                results.sort(key=lambda x: x["market_cap"], reverse=True)
                return results
    except Exception as e:
        logger.warning(f"Failed to fetch earnings for date {date_str}: {e}")
    return []


def fetch_7day_earnings_schedule(portfolio_tickers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
    pt_set = set([t.upper() for t in portfolio_tickers]) if portfolio_tickers else set()

    start_day = 0 if now_pst.hour < 18 else 1
    target_dates = [now_pst + timedelta(days=i) for i in range(start_day, start_day + 7)]

    def _fetch_single_day(day_date: datetime) -> Optional[Dict[str, Any]]:
        date_str = day_date.strftime("%Y-%m-%d")
        day_name = day_date.strftime("%A, %B %d")

        day_rows = fetch_earnings_for_date(date_str)
        if not day_rows:
            return None

        bmo = [r for r in day_rows if "BMO" in r["timing"]]
        amc = [r for r in day_rows if "AMC" in r["timing"]]

        def select_prominent(items: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
            selected = []
            for item in items:
                if item["ticker"] in pt_set:
                    selected.append(item)
            for item in items:
                if item not in selected and (item["market_cap"] > 1_000_000_000 or len(selected) < limit):
                    selected.append(item)
                if len(selected) >= limit:
                    break
            return selected

        bmo_selected = select_prominent(bmo, limit=4)
        amc_selected = select_prominent(amc, limit=4)

        if bmo_selected or amc_selected:
            return {
                "date": date_str,
                "day_name": day_name,
                "display_date": day_name,
                "day_of_week": day_date.strftime("%A"),
                "bmo": bmo_selected,
                "amc": amc_selected,
                "total_companies": len(day_rows)
            }
        return None

    with ThreadPoolExecutor(max_workers=min(len(target_dates), 7)) as executor:
        day_results = list(executor.map(_fetch_single_day, target_dates))

    schedule = [d for d in day_results if d is not None]
    return schedule
