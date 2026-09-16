"""
Unit tests for analytics/economic_calendar.py
"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from analytics.economic_calendar import (
    get_economic_calendar_context,
    format_economic_calendar_for_prompt,
    KEY_MACRO_RELEASES_2026,
    FOMC_SCHEDULE
)

PST = ZoneInfo("America/Los_Angeles")
EST = ZoneInfo("America/New_York")


def test_fomc_schedule_integrity():
    """Verify FOMC schedule has entries for 2025, 2026, and 2027."""
    assert len(FOMC_SCHEDULE) >= 16
    dates = [e["date"] for e in FOMC_SCHEDULE]
    assert "2026-09-16" in dates
    assert "2026-10-28" in dates
    assert "2026-12-09" in dates


def test_september_16_postmarket_completed_status():
    """
    On Sept 16, 2026 at 15:00 PST (18:00 EDT), the 14:00 EDT FOMC decision
    must be resolved as COMPLETED with a strict directive.
    """
    as_of = datetime(2026, 9, 16, 15, 0, tzinfo=PST)
    ctx = get_economic_calendar_context(as_of=as_of)

    assert ctx["today_date"] == "2026-09-16"
    assert ctx["has_completed_fomc_today"] is True
    assert ctx["has_fomc_tomorrow"] is False

    today_events = ctx["today_events"]
    assert len(today_events) >= 2  # Rate decision + Press conference

    fomc_event = next(e for e in today_events if "Decision" in e["name"])
    assert fomc_event["status"] == "COMPLETED"
    assert "Concluded earlier today" in fomc_event["status_desc"]
    assert "DO NOT describe it as upcoming, happening tomorrow" in fomc_event["directive"]

    prompt_str = format_economic_calendar_for_prompt(ctx)
    assert "[COMPLETED] Federal Reserve FOMC Interest Rate Decision" in prompt_str
    assert "TOMORROW'S SCHEDULED CATALYSTS (2026-09-17): No tier-1 macro events" in prompt_str


def test_september_16_premarket_pending_status():
    """
    On Sept 16, 2026 at 06:30 PST (09:30 EDT), the 14:00 EDT FOMC decision
    must be resolved as PENDING (upcoming later today).
    """
    as_of = datetime(2026, 9, 16, 6, 30, tzinfo=PST)
    ctx = get_economic_calendar_context(as_of=as_of)

    assert ctx["has_completed_fomc_today"] is False
    today_events = ctx["today_events"]
    fomc_event = next(e for e in today_events if "Decision" in e["name"])
    assert fomc_event["status"] == "PENDING"
    assert "Scheduled for today" in fomc_event["status_desc"]


def test_tomorrow_event_detection():
    """
    On Sept 15, 2026 at 15:00 PST, the Sept 16 FOMC decision
    must be detected as TOMORROW's catalyst.
    """
    as_of = datetime(2026, 9, 15, 15, 0, tzinfo=PST)
    ctx = get_economic_calendar_context(as_of=as_of)

    assert ctx["today_date"] == "2026-09-15"
    assert ctx["tomorrow_date"] == "2026-09-16"
    assert ctx["has_completed_fomc_today"] is False
    assert ctx["has_fomc_tomorrow"] is True

    tomorrow_events = ctx["tomorrow_events"]
    assert any("FOMC Interest Rate Decision" in e["name"] for e in tomorrow_events)

    prompt_str = format_economic_calendar_for_prompt(ctx)
    assert "TOMORROW'S SCHEDULED CATALYSTS (2026-09-16):" in prompt_str
    assert "[TOMORROW] Federal Reserve FOMC Interest Rate Decision" in prompt_str


def test_upcoming_7d_events():
    """
    Verify upcoming events in next 7 days are tracked.
    """
    as_of = datetime(2026, 9, 10, 10, 0, tzinfo=PST)
    ctx = get_economic_calendar_context(as_of=as_of)

    tomorrow_names = [e["name"] for e in ctx["tomorrow_events"]]
    upcoming_names = [e["name"] for e in ctx["upcoming_events_7d"]]

    assert any("Consumer Price Index" in n for n in tomorrow_names)
    assert any("FOMC Interest Rate Decision" in n for n in upcoming_names)
