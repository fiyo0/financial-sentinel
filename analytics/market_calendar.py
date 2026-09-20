# ==============================================================================
# FINANCIAL SENTINEL — MARKET CALENDAR UTILITIES
# ==============================================================================
"""
US Financial Market & NYSE Holiday Calendar Engine.
Calculates official NYSE holiday schedules, statutory BLS release shifts,
early market closure sessions (1:00 PM ET), and next trading session projection.
"""

from datetime import date, timedelta
from typing import Set, Tuple


def calculate_easter_sunday(year: int) -> date:
    """
    Computes Western Easter Sunday using the Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_factor = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_factor) // 451
    month = (h + l_factor - 7 * m + 114) // 31
    day = ((h + l_factor - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed_holiday(actual_date: date) -> date:
    """
    Applies standard NYSE holiday observance rules:
    - If Saturday, observed on preceding Friday.
    - If Sunday, observed on following Monday.
    """
    if actual_date.weekday() == 5:  # Saturday
        return actual_date - timedelta(days=1)
    elif actual_date.weekday() == 6:  # Sunday
        return actual_date + timedelta(days=1)
    return actual_date


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """
    Finds the nth occurrence of weekday (0=Mon, 1=Tue, ..., 6=Sun) in a given month.
    """
    first_day = date(year, month, 1)
    days_offset = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=days_offset + (n - 1) * 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """
    Finds the last occurrence of weekday in a given month.
    """
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = next_month_first - timedelta(days=1)
    days_offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=days_offset)


def us_market_holidays(year: int) -> Set[date]:
    """
    Returns all official NYSE holiday observance dates for a given year.
    Covers:
    - New Year's Day (Jan 1, observed)
    - Martin Luther King Jr. Day (3rd Monday in January)
    - Washington's Birthday / Presidents' Day (3rd Monday in February)
    - Good Friday (Friday before Easter)
    - Memorial Day (Last Monday in May)
    - Juneteenth National Independence Day (June 19, observed)
    - Independence Day (July 4, observed)
    - Labor Day (1st Monday in September)
    - Thanksgiving Day (4th Thursday in November)
    - Christmas Day (December 25, observed)
    """
    holidays: Set[date] = set()

    # 1. New Year's Day
    holidays.add(_observed_fixed_holiday(date(year, 1, 1)))

    # 2. Martin Luther King Jr. Day (3rd Monday in January)
    holidays.add(_nth_weekday_of_month(year, 1, 0, 3))

    # 3. Presidents' Day (3rd Monday in February)
    holidays.add(_nth_weekday_of_month(year, 2, 0, 3))

    # 4. Good Friday (2 days before Easter Sunday)
    easter = calculate_easter_sunday(year)
    good_friday = easter - timedelta(days=2)
    holidays.add(good_friday)

    # 5. Memorial Day (Last Monday in May)
    holidays.add(_last_weekday_of_month(year, 5, 0))

    # 6. Juneteenth (June 19, observed)
    holidays.add(_observed_fixed_holiday(date(year, 6, 19)))

    # 7. Independence Day (July 4, observed)
    holidays.add(_observed_fixed_holiday(date(year, 7, 4)))

    # 8. Labor Day (1st Monday in September)
    holidays.add(_nth_weekday_of_month(year, 9, 0, 1))

    # 9. Thanksgiving Day (4th Thursday in November)
    holidays.add(_nth_weekday_of_month(year, 11, 3, 4))

    # 10. Christmas Day (December 25, observed)
    holidays.add(_observed_fixed_holiday(date(year, 12, 25)))

    return holidays


def is_market_holiday(d: date) -> bool:
    """
    Returns True if date d is an official NYSE market holiday.
    """
    return d in us_market_holidays(d.year)


def get_early_close_dates(year: int) -> Set[date]:
    """
    Returns scheduled early closure dates (market closes at 1:00 PM ET):
    - July 3rd (if July 4th is a Tuesday, Wednesday, Thursday, or Friday)
    - Day after Thanksgiving (Black Friday - 4th Friday in November)
    - Christmas Eve (December 24th, if falling on a Monday-Thursday)
    """
    early_closes: Set[date] = set()

    # Day after Thanksgiving
    thanksgiving = _nth_weekday_of_month(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    early_closes.add(black_friday)

    # July 3rd early close if July 4th is weekday
    july_4 = date(year, 7, 4)
    if july_4.weekday() in (1, 2, 3, 4):  # Tue-Fri
        early_closes.add(date(year, 7, 3))

    # Christmas Eve (Dec 24) early close if weekday and not observed holiday
    dec_24 = date(year, 12, 24)
    if dec_24.weekday() in (0, 1, 2, 3):  # Mon-Thu
        early_closes.add(dec_24)

    return early_closes


def get_market_close_time_et(d: date) -> Tuple[int, int]:
    """
    Returns the scheduled market close time (hour, minute) in Eastern Time.
    Standard: (16, 0) -> 4:00 PM ET.
    Scheduled early close: (13, 0) -> 1:00 PM ET.
    """
    if d in get_early_close_dates(d.year):
        return (13, 0)
    return (16, 0)


def get_next_trading_day(from_date: date) -> date:
    """
    Finds the next active trading day skipping weekends and NYSE holidays.
    """
    candidate = from_date + timedelta(days=1)
    while candidate.weekday() >= 5 or is_market_holiday(candidate):
        candidate += timedelta(days=1)
    return candidate


def shift_if_holiday(d: date) -> date:
    """
    If date d falls on a weekend or an official market holiday,
    rolls backward to the immediately preceding active trading day.
    Used for statutory macro releases (e.g. BLS NFP on July 4th Friday -> July 3rd Thursday).
    """
    candidate = d
    while candidate.weekday() >= 5 or is_market_holiday(candidate):
        candidate -= timedelta(days=1)
    return candidate
