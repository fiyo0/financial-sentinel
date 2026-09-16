"""
analytics/economic_calendar.py - Deterministic Macroeconomic & Central Bank Calendar Engine.
Tracks scheduled Federal Reserve FOMC rate decisions, press conferences, minutes,
and high-impact economic releases (CPI, PPI, Jobs/NFP, GDP) with exact timestamps
and real-time completion status.
"""
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

PST_TZ = ZoneInfo("America/Los_Angeles")
EST_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

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


def get_economic_calendar_context(
    as_of: Optional[datetime] = None,
    timezone_str: str = "America/Los_Angeles"
) -> Dict[str, Any]:
    """
    Evaluates the macroeconomic and central bank calendar against the reference timestamp.
    Determines status (COMPLETED vs PENDING/IMMINENT) for today's events, identifies tomorrow's
    catalysts, and lists key releases for the upcoming 7 calendar days.
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

    today_events = []
    tomorrow_events = []
    upcoming_events_7d = []

    for event in KEY_MACRO_RELEASES_2026:
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
                "elapsed_hours": diff_hours if is_completed else 0.0
            })

        elif ev_date == tomorrow_et_date:
            tomorrow_events.append({
                **event,
                "event_datetime_et": ev_dt.isoformat(),
                "event_datetime_user": ev_user_dt.isoformat(),
                "time_display": time_display,
                "status": "TOMORROW",
                "status_desc": f"Scheduled for tomorrow at {time_display}"
            })

        elif today_et_date < ev_date <= end_7d_date:
            upcoming_events_7d.append({
                **event,
                "event_datetime_et": ev_dt.isoformat(),
                "event_datetime_user": ev_user_dt.isoformat(),
                "time_display": f"{ev_dt.strftime('%A, %b %d')} at {time_display}",
                "status": "UPCOMING",
                "days_away": (ev_date - today_et_date).days
            })

    return {
        "as_of_user": as_of_user.isoformat(),
        "as_of_et": as_of_et.isoformat(),
        "today_date": str(today_et_date),
        "tomorrow_date": str(tomorrow_et_date),
        "today_events": today_events,
        "tomorrow_events": tomorrow_events,
        "upcoming_events_7d": upcoming_events_7d,
        "has_completed_fomc_today": any(e["status"] == "COMPLETED" and e["category"] == "CENTRAL_BANK" for e in today_events),
        "has_fomc_tomorrow": any(e["category"] == "CENTRAL_BANK" for e in tomorrow_events)
    }


def format_economic_calendar_for_prompt(calendar_ctx: Dict[str, Any]) -> str:
    """
    Builds a high-visibility, authoritative macroeconomic ground-truth context block
    for injection into CIO briefing prompts.
    """
    lines = [
        "🏛️ MACROECONOMIC & CENTRAL BANK CALENDAR GROUND TRUTH:"
    ]

    today_events = calendar_ctx.get("today_events", [])
    tomorrow_events = calendar_ctx.get("tomorrow_events", [])
    upcoming = calendar_ctx.get("upcoming_events_7d", [])

    if today_events:
        lines.append(f"• TODAY'S EVENTS ({calendar_ctx.get('today_date')}):")
        for ev in today_events:
            lines.append(f"  - [{ev['status']}] {ev['name']}")
            lines.append(f"    Timing: {ev['status_desc']}")
            lines.append(f"    Instruction: {ev['directive']}")
    else:
        lines.append(f"• TODAY'S EVENTS ({calendar_ctx.get('today_date')}): No major tier-1 macroeconomic releases scheduled today.")

    if tomorrow_events:
        lines.append(f"• TOMORROW'S SCHEDULED CATALYSTS ({calendar_ctx.get('tomorrow_date')}):")
        for ev in tomorrow_events:
            lines.append(f"  - [TOMORROW] {ev['name']} ({ev['time_display']})")
    else:
        lines.append(f"• TOMORROW'S SCHEDULED CATALYSTS ({calendar_ctx.get('tomorrow_date')}): No tier-1 macro events scheduled for tomorrow.")

    if upcoming:
        lines.append("• UPCOMING (NEXT 7 DAYS):")
        for ev in upcoming[:4]:
            lines.append(f"  - {ev['name']} ({ev['time_display']})")

    return "\n".join(lines)
