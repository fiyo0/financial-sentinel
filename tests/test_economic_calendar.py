"""
Unit tests for analytics/economic_calendar.py
"""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from analytics.economic_calendar import (
    get_economic_calendar_context,
    format_economic_calendar_for_prompt,
    generate_economic_calendar_ics,
    fetch_live_fed_bulletins,
    escape_ics_text,
    _macro_cache,
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


def test_generate_economic_calendar_ics_rfc5545():
    """
    Verify RFC 5545 compliance: VCALENDAR markers, VEVENT fields,
    deterministic UIDs, and pre-event VALARM trading reminders.
    """
    ics = generate_economic_calendar_ics(catalytic_only=True)

    # Standard RFC 5545 markers
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0\r\n" in ics
    assert "PRODID:-//Financial Sentinel//Macro Catalytic Calendar Engine//EN\r\n" in ics
    assert "CALSCALE:GREGORIAN\r\n" in ics
    assert "X-WR-CALNAME:Financial Sentinel Macro Catalysts\r\n" in ics

    # VEVENT structure & fields
    assert "BEGIN:VEVENT\r\n" in ics
    assert "END:VEVENT\r\n" in ics
    assert "UID:macro-2026-09-16-1400-" in ics
    assert "@financial-sentinel\r\n" in ics
    assert "DTSTART:20260916T180000Z\r\n" in ics
    assert "STATUS:CONFIRMED\r\n" in ics
    assert "CATEGORIES:FINANCE,MACRO," in ics

    # Pre-event Alarms (-15m and -1h)
    assert "BEGIN:VALARM\r\n" in ics
    assert "TRIGGER:-PT15M\r\n" in ics
    assert "TRIGGER:-PT1H\r\n" in ics
    assert "ACTION:DISPLAY\r\n" in ics


def test_generate_economic_calendar_ics_catalytic_filtering():
    """
    When catalytic_only=True, tier-2 releases like PPI should be omitted,
    while tier-1 catalytic events like FOMC decisions and CPI must be retained.
    When catalytic_only=False, PPI must be included.
    """
    ics_catalytic = generate_economic_calendar_ics(catalytic_only=True)
    assert "FOMC Interest Rate Decision" in ics_catalytic
    assert "Consumer Price Index" in ics_catalytic
    assert "Producer Price Index" not in ics_catalytic

    ics_all = generate_economic_calendar_ics(catalytic_only=False)
    assert "Producer Price Index" in ics_all
    assert "FOMC Interest Rate Decision" in ics_all


def test_escape_ics_text():
    """Verify proper escaping of special characters in RFC 5545 text fields."""
    raw = "Federal Reserve, Rates; Projections & Cuts\nNext line\\test"
    escaped = escape_ics_text(raw)
    assert "\\," in escaped
    assert "\\;" in escaped
    assert "\\n" in escaped
    assert "\\\\" in escaped


def test_catalytic_only_context_flag():
    """Verify get_economic_calendar_context respects catalytic_only parameter."""
    as_of = datetime(2026, 9, 11, 12, 0, tzinfo=EST)  # Day of CPI release (Sept 11)
    ctx_cat = get_economic_calendar_context(as_of=as_of, catalytic_only=True)
    ctx_all = get_economic_calendar_context(as_of=as_of, catalytic_only=False)

    assert ctx_cat["catalytic_only"] is True
    assert ctx_all["catalytic_only"] is False
    assert "feed_urls" in ctx_cat
    assert ctx_cat["feed_urls"]["ics"] == "/api/economic/calendar.ics"

    # In catalytic mode, tomorrow (Sept 12 PPI) should be empty because PPI is MEDIUM
    assert len(ctx_cat["tomorrow_events"]) == 0
    # In all mode, tomorrow (Sept 12 PPI) should be present
    assert any("Producer Price Index" in e["name"] for e in ctx_all["tomorrow_events"])


def test_fetch_live_fed_bulletins_mock(monkeypatch):
    """
    Test parsing of live Federal Reserve monetary RSS feeds with mock payload.
    """
    _macro_cache.clear("macro_calendar")

    mock_rss_xml = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Federal Reserve Board - Press Releases</title>
        <link>https://www.federalreserve.gov</link>
        <item>
          <title>Federal Open Market Committee announces 25 bps rate cut to 5.00-5.25 percent</title>
          <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm</link>
          <description>Recent indicators suggest that economic activity has continued to expand at a solid pace.</description>
          <pubDate>Wed, 16 Sep 2026 14:00:00 -0400</pubDate>
        </item>
      </channel>
    </rss>
    """

    class MockResponse:
        status_code = 200
        text = mock_rss_xml

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            return MockResponse()

    monkeypatch.setattr("httpx.Client", MockClient)

    bulletins = fetch_live_fed_bulletins(max_age_hours=100000.0)
    assert len(bulletins) >= 1
    bulletin = bulletins[0]
    assert "25 bps rate cut" in bulletin["title"]
    assert bulletin["is_rate_action"] is True
    assert "federalreserve.gov" in bulletin["link"]

    # Test prompt formatting with live bulletin injection
    ctx = {
        "today_date": "2026-09-16",
        "tomorrow_date": "2026-09-17",
        "today_events": [],
        "tomorrow_events": [],
        "upcoming_events_7d": [],
        "live_fed_bulletins": bulletins
    }
    prompt_str = format_economic_calendar_for_prompt(ctx)
    assert "LIVE FEDERAL RESERVE MONETARY POLICY PULSE" in prompt_str
    assert "25 bps rate cut" in prompt_str

