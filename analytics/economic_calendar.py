"""
analytics/economic_calendar.py - Deterministic & Live Macroeconomic / Central Bank Calendar Suite.
Tracks scheduled Federal Reserve FOMC rate decisions, press conferences, minutes,
and catalytic high-impact economic releases (CPI, PPI, Jobs/NFP, PCE, GDP) with exact timestamps,
real-time completion status, live Federal Reserve policy bulletins, and RFC 5545 iCalendar sync.
"""
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
import logging
import re
import feedparser
import httpx
from storage.cache_manager import CacheManager

logger = logging.getLogger(__name__)

PST_TZ = ZoneInfo("America/Los_Angeles")
EST_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

# Centralized in-memory cache for live external macro feeds (15-minute TTL)
_macro_cache = CacheManager(default_ttl_seconds=900.0)

# Federal Reserve Official RSS Endpoints
FED_MONETARY_FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
FED_ALL_PRESS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"

# Official Federal Reserve FOMC Calendar (2025 - 2027)
# Announcement: 2:00 PM Eastern Time (14:00), Press Conference: 2:30 PM Eastern Time (14:30)
FOMC_SCHEDULE = [
    # 2025
    {"date": "2025-01-29", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2025-03-19", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2025-05-07", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2025-06-18", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2025-07-30", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2025-09-17", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2025-10-29", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2025-12-10", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    # 2026
    {"date": "2026-01-28", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2026-03-18", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2026-04-29", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2026-06-17", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2026-07-29", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2026-09-16", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    {"date": "2026-10-28", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2026-12-09", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
    # 2027
    {"date": "2027-01-27", "time_et": "14:00", "has_sep": False, "desc": "FOMC Rate Decision"},
    {"date": "2027-03-17", "time_et": "14:00", "has_sep": True, "desc": "FOMC Rate Decision & Economic Projections (SEP)"},
]

# Major Monthly US Macro Catalysts Schedule Patterns (2026 Ground Truth)
# Format: date, time_et, category, name, importance (HIGH/CRITICAL)
KEY_MACRO_RELEASES_2026 = [
    # September 2026
    {"date": "2026-09-04", "time_et": "08:30", "category": "LABOR", "name": "U.S. Employment Situation (Non-Farm Payrolls)", "importance": "HIGH"},
    {"date": "2026-09-11", "time_et": "08:30", "category": "INFLATION", "name": "Consumer Price Index (CPI)", "importance": "HIGH"},
    {"date": "2026-09-12", "time_et": "08:30", "category": "INFLATION", "name": "Producer Price Index (PPI)", "importance": "MEDIUM"},
    {"date": "2026-09-16", "time_et": "14:00", "category": "CENTRAL_BANK", "name": "Federal Reserve FOMC Interest Rate Decision & Summary of Economic Projections", "importance": "CRITICAL"},
    {"date": "2026-09-16", "time_et": "14:30", "category": "CENTRAL_BANK", "name": "Federal Reserve Chair Press Conference", "importance": "CRITICAL"},
    {"date": "2026-09-24", "time_et": "08:30", "category": "GROWTH", "name": "Q2 GDP Final Estimate", "importance": "MEDIUM"},
    # October 2026
    {"date": "2026-10-02", "time_et": "08:30", "category": "LABOR", "name": "U.S. Employment Situation (Non-Farm Payrolls)", "importance": "HIGH"},
    {"date": "2026-10-07", "time_et": "14:00", "category": "CENTRAL_BANK", "name": "FOMC September Meeting Minutes", "importance": "MEDIUM"},
    {"date": "2026-10-14", "time_et": "08:30", "category": "INFLATION", "name": "Consumer Price Index (CPI)", "importance": "HIGH"},
    {"date": "2026-10-15", "time_et": "08:30", "category": "INFLATION", "name": "Producer Price Index (PPI)", "importance": "MEDIUM"},
    {"date": "2026-10-28", "time_et": "14:00", "category": "CENTRAL_BANK", "name": "Federal Reserve FOMC Interest Rate Decision", "importance": "CRITICAL"},
    {"date": "2026-10-28", "time_et": "14:30", "category": "CENTRAL_BANK", "name": "Federal Reserve Chair Press Conference", "importance": "CRITICAL"},
    {"date": "2026-10-29", "time_et": "08:30", "category": "GROWTH", "name": "Q3 GDP Advance Estimate", "importance": "HIGH"},
    # November 2026
    {"date": "2026-11-06", "time_et": "08:30", "category": "LABOR", "name": "U.S. Employment Situation (Non-Farm Payrolls)", "importance": "HIGH"},
    {"date": "2026-11-12", "time_et": "08:30", "category": "INFLATION", "name": "Consumer Price Index (CPI)", "importance": "HIGH"},
    {"date": "2026-11-18", "time_et": "14:00", "category": "CENTRAL_BANK", "name": "FOMC October Meeting Minutes", "importance": "MEDIUM"},
    # December 2026
    {"date": "2026-12-04", "time_et": "08:30", "category": "LABOR", "name": "U.S. Employment Situation (Non-Farm Payrolls)", "importance": "HIGH"},
    {"date": "2026-12-09", "time_et": "14:00", "category": "CENTRAL_BANK", "name": "Federal Reserve FOMC Interest Rate Decision & Summary of Economic Projections", "importance": "CRITICAL"},
    {"date": "2026-12-09", "time_et": "14:30", "category": "CENTRAL_BANK", "name": "Federal Reserve Chair Press Conference", "importance": "CRITICAL"},
    {"date": "2026-12-11", "time_et": "08:30", "category": "INFLATION", "name": "Consumer Price Index (CPI)", "importance": "HIGH"},
]


def _build_event_datetime(date_str: str, time_et_str: str) -> datetime:
    """Combines a date string YYYY-MM-DD and Eastern time HH:MM into an aware datetime."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    h, m = map(int, time_et_str.split(":"))
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=EST_TZ)


def escape_ics_text(text: str) -> str:
    """Escapes special characters for RFC 5545 iCalendar text values."""
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n")
    return text


def fetch_live_fed_bulletins(max_age_hours: float = 72.0, timeout: float = 3.0) -> List[Dict[str, Any]]:
    """
    Ingests live Federal Reserve monetary policy and press RSS feeds.
    Extracts official FOMC actions, interest rate adjustments, and policy statements.
    Caches parsed feeds in memory for 15 minutes. Gracefully returns empty list if
    network is offline or rate-limited.
    """
    cached = _macro_cache.get("macro_calendar", "fed_bulletins")
    if cached is not None:
        return cached

    bulletins: List[Dict[str, Any]] = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FinancialSentinel/2.4; MacroIntelligenceEngine)"}
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(FED_MONETARY_FEED_URL)
            if resp.status_code == 200 and resp.text:
                parsed = feedparser.parse(resp.text)
                now_utc = datetime.now(UTC_TZ)
                for entry in parsed.entries[:8]:
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "").strip()
                    summary = getattr(entry, "summary", "").strip()
                    published_parsed = getattr(entry, "published_parsed", None)

                    if published_parsed:
                        pub_dt = datetime(*published_parsed[:6], tzinfo=UTC_TZ)
                        age_hours = max(0.0, (now_utc - pub_dt).total_seconds() / 3600.0)
                        if age_hours > max_age_hours:
                            continue
                    else:
                        pub_dt = now_utc
                        age_hours = 0.0

                    is_rate_action = any(
                        kw in title.lower() or kw in summary.lower()
                        for kw in ["federal open market committee", "fomc statement", "discount rate", "policy action", "interest rate", "target range"]
                    )

                    bulletins.append({
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": getattr(entry, "published", pub_dt.strftime("%Y-%m-%d %H:%M UTC")),
                        "published_iso": pub_dt.isoformat(),
                        "age_hours": round(age_hours, 1),
                        "is_rate_action": is_rate_action,
                        "source": "Federal Reserve Board (Official Monetary Feed)"
                    })
    except Exception as exc:
        logger.debug("Live Federal Reserve feed fetch skipped or unavailable: %s", exc)

    _macro_cache.set("macro_calendar", "fed_bulletins", bulletins, ttl_seconds=900.0)
    return bulletins


def generate_economic_calendar_ics(
    catalytic_only: bool = True,
    calendar_name: str = "Financial Sentinel Macro Catalysts"
) -> str:
    """
    Generates a standards-compliant RFC 5545 iCalendar (.ics) subscription string.
    Includes scheduled FOMC rate decisions, press conferences, CPI, PPI, NFP, and GDP releases.
    Configures pre-event trading alarms (-15m and -1h) and unique deterministic UIDs.
    """
    events_to_include = []
    seen_keys = set()

    # 1. Collect Key Macro Releases
    for ev in KEY_MACRO_RELEASES_2026:
        is_catalytic = ev.get("importance") in ("CRITICAL", "HIGH") or ev.get("category") == "CENTRAL_BANK"
        if catalytic_only and not is_catalytic:
            continue
        key = (ev["date"], ev["time_et"], ev["name"])
        if key not in seen_keys:
            seen_keys.add(key)
            events_to_include.append(ev)

    # 2. Collect FOMC schedule across 2025-2027
    for fomc in FOMC_SCHEDULE:
        # Main Rate Decision
        fomc_name = fomc["desc"]
        key = (fomc["date"], fomc["time_et"], fomc_name)
        if key not in seen_keys:
            seen_keys.add(key)
            events_to_include.append({
                "date": fomc["date"],
                "time_et": fomc["time_et"],
                "category": "CENTRAL_BANK",
                "name": fomc_name,
                "importance": "CRITICAL"
            })

        # Accompanying Press Conference (30 minutes after rate decision)
        presser_name = "Federal Reserve Chair Press Conference"
        key_presser = (fomc["date"], "14:30", presser_name)
        if key_presser not in seen_keys:
            seen_keys.add(key_presser)
            events_to_include.append({
                "date": fomc["date"],
                "time_et": "14:30",
                "category": "CENTRAL_BANK",
                "name": presser_name,
                "importance": "CRITICAL"
            })

    # Sort chronologically
    events_to_include.sort(key=lambda x: (x["date"], x["time_et"]))

    dtstamp = datetime.now(UTC_TZ).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Financial Sentinel//Macro Catalytic Calendar Engine//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics_text(calendar_name)}",
        "X-WR-TIMEZONE:America/New_York",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H"
    ]

    for ev in events_to_include:
        ev_dt = _build_event_datetime(ev["date"], ev["time_et"])
        ev_utc = ev_dt.astimezone(UTC_TZ)
        duration_minutes = 60 if "Press Conference" in ev["name"] else 30
        ev_end_utc = ev_utc + timedelta(minutes=duration_minutes)

        start_str = ev_utc.strftime("%Y%m%dT%H%M%SZ")
        end_str = ev_end_utc.strftime("%Y%m%dT%H%M%SZ")

        safe_name = re.sub(r'[^a-zA-Z0-9]', '-', ev["name"].lower())[:32].strip('-')
        uid = f"macro-{ev['date']}-{ev['time_et'].replace(':', '')}-{safe_name}@financial-sentinel"

        badge = "🏛️ [CRITICAL]" if ev.get("importance") == "CRITICAL" else "⚡ [HIGH]" if ev.get("importance") == "HIGH" else "📊 [MACRO]"
        summary = f"{badge} {ev['name']}"

        desc_parts = [
            f"Category: {ev.get('category', 'MACRO')}",
            f"Importance: {ev.get('importance', 'HIGH')}",
            f"Scheduled Time: {ev['time_et']} Eastern Time / {_build_event_datetime(ev['date'], ev['time_et']).astimezone(PST_TZ).strftime('%I:%M %p')} Pacific",
            "Trading Directive: Monitor implied volatility, rate curve reaction, and cross-asset liquidity.",
            "Live Trading Desk: Financial Sentinel Portfolio Intelligence"
        ]
        description = "\\n\\n".join(desc_parts)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{start_str}",
            f"DTEND:{end_str}",
            f"SUMMARY:{escape_ics_text(summary)}",
            f"DESCRIPTION:{description}",
            f"CATEGORIES:FINANCE,MACRO,{ev.get('category', 'MACRO')},CATALYST",
            "STATUS:CONFIRMED",
            "SEQUENCE:0",
            # Alarm 1: 15 minutes before event
            "BEGIN:VALARM",
            "TRIGGER:-PT15M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Financial Sentinel Reminder: {escape_ics_text(ev['name'])} in 15 minutes",
            "END:VALARM",
            # Alarm 2: 1 hour before event
            "BEGIN:VALARM",
            "TRIGGER:-PT1H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Financial Sentinel Reminder: {escape_ics_text(ev['name'])} in 1 hour",
            "END:VALARM",
            "END:VEVENT"
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def get_economic_calendar_context(
    as_of: Optional[datetime] = None,
    timezone_str: str = "America/Los_Angeles",
    catalytic_only: bool = False,
    include_live_bulletins: bool = True,
    horizon_days: int = 90
) -> Dict[str, Any]:
    """
    Evaluates the macroeconomic and central bank calendar against the reference timestamp.
    Determines status (COMPLETED vs PENDING/IMMINENT) for today's events, identifies tomorrow's
    catalysts, and lists key releases for both the immediate 7-day window and prospective 3-month (90-day) horizon.
    Also incorporates live Federal Reserve policy bulletins and live iCalendar sync URLs.
    """
    user_tz = ZoneInfo(timezone_str)
    if as_of is None:
        as_of_user = datetime.now(user_tz)
    elif as_of.tzinfo is None:
        as_of_user = as_of.replace(tzinfo=user_tz)
    else:
        as_of_user = as_of.astimezone(user_tz)

    as_of_et = as_of_user.astimezone(EST_TZ)
    today_et_date = as_of_et.date()
    tomorrow_et_date = today_et_date + timedelta(days=1)
    end_7d_date = today_et_date + timedelta(days=7)
    end_horizon_date = today_et_date + timedelta(days=horizon_days)

    today_events = []
    tomorrow_events = []
    upcoming_events_7d = []
    upcoming_events_90d = []

    # Merge monthly release table and multi-year FOMC schedule
    events_pool = []
    seen_keys = set()
    for ev in KEY_MACRO_RELEASES_2026:
        key = (ev["date"], ev["time_et"], ev["name"])
        seen_keys.add(key)
        events_pool.append(ev)

    for fomc in FOMC_SCHEDULE:
        key_rate = (fomc["date"], fomc["time_et"], fomc["desc"])
        if key_rate not in seen_keys:
            seen_keys.add(key_rate)
            events_pool.append({
                "date": fomc["date"],
                "time_et": fomc["time_et"],
                "category": "CENTRAL_BANK",
                "name": fomc["desc"],
                "importance": "CRITICAL"
            })
        key_presser = (fomc["date"], "14:30", "Federal Reserve Chair Press Conference")
        if key_presser not in seen_keys:
            seen_keys.add(key_presser)
            events_pool.append({
                "date": fomc["date"],
                "time_et": "14:30",
                "category": "CENTRAL_BANK",
                "name": "Federal Reserve Chair Press Conference",
                "importance": "CRITICAL"
            })

    events_pool.sort(key=lambda x: (x["date"], x["time_et"]))

    for event in events_pool:
        is_catalytic = event.get("importance") in ("CRITICAL", "HIGH") or event.get("category") == "CENTRAL_BANK"
        if catalytic_only and not is_catalytic:
            continue

        ev_dt = _build_event_datetime(event["date"], event["time_et"])
        ev_date = ev_dt.astimezone(EST_TZ).date()
        ev_user_dt = ev_dt.astimezone(user_tz)

        time_display = f"{ev_dt.strftime('%I:%M %p')} EDT ({ev_user_dt.strftime('%I:%M %p')} {user_tz.key.split('/')[-1]})"

        if ev_date == today_et_date:
            is_completed = as_of_et >= ev_dt
            diff_hours = (as_of_et - ev_dt).total_seconds() / 3600.0

            if is_completed:
                status = "COMPLETED"
                status_desc = f"Concluded earlier today ({diff_hours:.1f}h ago at {ev_dt.strftime('%I:%M %p')} EDT)"
                directive = (
                    "CRITICAL DIRECTIVE: This catalyst CONCLUDED EARLIER TODAY. "
                    "Analyze its outcome, market reaction, and closing bell aftermath. "
                    "DO NOT describe it as upcoming, happening tomorrow, or in the future."
                )
            else:
                remaining_hours = abs(diff_hours)
                status = "IMMINENT" if remaining_hours < 2.0 else "PENDING"
                status_desc = f"Scheduled for today at {time_display} (in {remaining_hours:.1f}h)"
                directive = f"Upcoming today during the trading session at {time_display}."

            today_events.append({
                **event,
                "event_datetime_et": ev_dt.isoformat(),
                "event_datetime_user": ev_user_dt.isoformat(),
                "time_display": time_display,
                "status": status,
                "status_desc": status_desc,
                "directive": directive,
                "elapsed_hours": diff_hours if is_completed else 0.0,
                "is_catalytic": is_catalytic
            })

        elif ev_date == tomorrow_et_date:
            tomorrow_events.append({
                **event,
                "event_datetime_et": ev_dt.isoformat(),
                "event_datetime_user": ev_user_dt.isoformat(),
                "time_display": time_display,
                "status": "TOMORROW",
                "status_desc": f"Scheduled for tomorrow at {time_display}",
                "is_catalytic": is_catalytic
            })

        elif today_et_date < ev_date <= end_horizon_date:
            days_away = (ev_date - today_et_date).days
            upcoming_entry = {
                **event,
                "event_datetime_et": ev_dt.isoformat(),
                "event_datetime_user": ev_user_dt.isoformat(),
                "time_display": f"{ev_dt.strftime('%A, %b %d')} at {time_display}",
                "status": "UPCOMING",
                "days_away": days_away,
                "is_catalytic": is_catalytic
            }
            upcoming_events_90d.append(upcoming_entry)
            if ev_date <= end_7d_date:
                upcoming_events_7d.append(upcoming_entry)

    # Ingest Live Federal Reserve Monetary Bulletins
    live_bulletins = []
    if include_live_bulletins:
        try:
            live_bulletins = fetch_live_fed_bulletins(max_age_hours=72.0)
        except Exception as e:
            logger.debug("Live Fed bulletin retrieval failed: %s", e)

    return {
        "as_of_user": as_of_user.isoformat(),
        "as_of_et": as_of_et.isoformat(),
        "today_date": str(today_et_date),
        "tomorrow_date": str(tomorrow_et_date),
        "catalytic_only": catalytic_only,
        "horizon_days": horizon_days,
        "today_events": today_events,
        "tomorrow_events": tomorrow_events,
        "upcoming_events": upcoming_events_90d,
        "upcoming_events_90d": upcoming_events_90d,
        "upcoming_events_7d": upcoming_events_7d,
        "has_completed_fomc_today": any(e["status"] == "COMPLETED" and e["category"] == "CENTRAL_BANK" for e in today_events),
        "has_fomc_tomorrow": any(e["category"] == "CENTRAL_BANK" for e in tomorrow_events),
        "live_fed_bulletins": live_bulletins,
        "feed_urls": {
            "ics": "/api/economic/calendar.ics",
            "ics_catalytic": "/api/economic/calendar.ics?catalytic_only=true",
        },
        "stats": {
            "today_count": len(today_events),
            "tomorrow_count": len(tomorrow_events),
            "upcoming_count": len(upcoming_events_90d),
            "upcoming_7d_count": len(upcoming_events_7d),
            "upcoming_90d_count": len(upcoming_events_90d),
            "live_bulletin_count": len(live_bulletins)
        }
    }


def format_economic_calendar_for_prompt(calendar_ctx: Dict[str, Any], include_horizon: bool = False) -> str:
    """
    Builds a high-visibility, authoritative macroeconomic ground-truth context block
    for injection into CIO briefing prompts.
    """
    lines = [
        "🏛️ MACROECONOMIC & CENTRAL BANK CALENDAR GROUND TRUTH:"
    ]

    # Live Federal Reserve Bulletins (if active)
    live_bulletins = calendar_ctx.get("live_fed_bulletins", [])
    if live_bulletins:
        lines.append("⚡ LIVE FEDERAL RESERVE MONETARY POLICY PULSE (RECENT BULLETINS):")
        for b in live_bulletins[:2]:
            lines.append(f"  - [LIVE ANNOUNCEMENT] {b.get('title')}")
            lines.append(f"    Published: {b.get('published', 'Recent')} | Source: {b.get('source')}")

    today_events = calendar_ctx.get("today_events", [])
    tomorrow_events = calendar_ctx.get("tomorrow_events", [])
    upcoming_7d = calendar_ctx.get("upcoming_events_7d", [])
    upcoming_3m = calendar_ctx.get("upcoming_events_90d", calendar_ctx.get("upcoming_events", []))

    if today_events:
        lines.append(f"• TODAY'S EVENTS ({calendar_ctx.get('today_date')}):")
        for ev in today_events:
            lines.append(f"  - [{ev['status']}] {ev['name']}")
            lines.append(f"    Timing: {ev['status_desc']}")
            lines.append(f"    Instruction: {ev['directive']}")
    else:
        lines.append(f"• TODAY'S EVENTS ({calendar_ctx.get('today_date')}): None scheduled.")

    if tomorrow_events:
        lines.append(f"• TOMORROW'S SCHEDULED CATALYSTS ({calendar_ctx.get('tomorrow_date')}):")
        for ev in tomorrow_events:
            lines.append(f"  - [TOMORROW] {ev['name']} ({ev['time_display']})")
    else:
        lines.append(f"• TOMORROW'S SCHEDULED CATALYSTS ({calendar_ctx.get('tomorrow_date')}): None scheduled.")

    if upcoming_7d:
        lines.append("• UPCOMING (NEXT 7 DAYS):")
        for ev in upcoming_7d[:4]:
            lines.append(f"  - {ev['name']} ({ev['time_display']})")

    # Forward 3-Month Schedule (only included for weekend/weekly previews or when explicitly requested)
    if include_horizon:
        beyond_7d = [e for e in upcoming_3m if e.get("days_away", 0) > 7 and (e.get("importance") == "CRITICAL" or e.get("category") == "CENTRAL_BANK")]
        if beyond_7d:
            lines.append("• PROSPECTIVE 3-MONTH CENTRAL BANK SCHEDULE:")
            for ev in beyond_7d[:5]:
                lines.append(f"  - [{ev.get('importance', 'HIGH')}] {ev['name']} ({ev['time_display']}) [in {ev.get('days_away')}d]")

    if not today_events and not tomorrow_events:
        lines.append("• NOTE ON MACRO DATA: No releases scheduled today or tomorrow. Do NOT mention the absence of data or write filler about a quiet macro calendar. Focus directly on market price action, sector flows, and company catalysts.")

    return "\n".join(lines)


