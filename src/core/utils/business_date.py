"""
Business Date Utilities

The cafe operates until 5:00 AM IST, so a "business day" runs from
05:00:00 on Day 1 to 04:59:59 on Day 2 (IST).

All analytics should use these utilities for consistent date handling.
"""
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Iterator, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_TIMEZONE = "Asia/Kolkata"
BUSINESS_DAY_START_HOUR = 5  # 5:00 AM local restaurant time

# SQL fragment for SQLite to calculate business date
# Subtracts 5 hours from the stored local timestamp to align with the business day
# e.g., '2023-01-01 02:00:00' -> '2022-12-31 21:00:00' -> DATE(...) -> '2022-12-31'
# Order rows are stored in the restaurant's own local time, so the 5-hour cutoff
# is timezone-independent here; only "what is today" needs the profile timezone.
BUSINESS_DATE_SQL = "DATE(created_on, '-5 hours')"


@dataclass(frozen=True)
class BusinessDateContext:
    """The profile timezone and the one instant a request treats as "now".

    An All Stores request captures a single instant and evaluates each profile's
    business date in that profile's own timezone, so two stores can never
    disagree about which moment "today" was computed from.
    """

    timezone: str = DEFAULT_TIMEZONE
    as_of: Optional[datetime] = None

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone or DEFAULT_TIMEZONE)
        except Exception:
            return IST

    def now(self) -> datetime:
        if self.as_of is not None:
            return self.as_of.astimezone(self.zone())
        return datetime.now(self.zone())


_BUSINESS_DATE_CONTEXT: contextvars.ContextVar[Optional[BusinessDateContext]] = (
    contextvars.ContextVar("business_date_context", default=None)
)


def current_business_date_context() -> BusinessDateContext:
    return _BUSINESS_DATE_CONTEXT.get() or BusinessDateContext()


@contextmanager
def business_date_context(
    timezone: Optional[str] = None, as_of: Optional[datetime] = None
) -> Iterator[BusinessDateContext]:
    """Bind the profile timezone (and optionally one captured instant)."""
    inherited = _BUSINESS_DATE_CONTEXT.get()
    context = BusinessDateContext(
        timezone=timezone or (inherited.timezone if inherited else DEFAULT_TIMEZONE),
        as_of=as_of if as_of is not None else (inherited.as_of if inherited else None),
    )
    token = _BUSINESS_DATE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _BUSINESS_DATE_CONTEXT.reset(token)


def get_current_business_date(now: Optional[datetime] = None) -> str:
    """
    Get the current business date in YYYY-MM-DD format for the active profile.

    If the local time is before 5 AM, returns yesterday's date.
    Else returns today's date.
    """
    local_now = now.astimezone(current_business_date_context().zone()) if now else current_business_date_context().now()
    if local_now.hour < BUSINESS_DAY_START_HOUR:
        return (local_now.date() - timedelta(days=1)).isoformat()
    return local_now.date().isoformat()


def get_last_complete_business_date() -> str:
    """
    Get the last fully completed business date (safe for training).
    
    The current business day is still in progress, so we return
    one day before the current business date.
    
    Example:
        If now is Feb 8, 17:30 (after 5am), current business date is Feb 8.
        Last complete business date is Feb 7.
        
        If now is Feb 9, 03:00 (before 5am), current business date is Feb 8.
        Last complete business date is Feb 7.
    """
    current_bd = datetime.fromisoformat(get_current_business_date())
    return (current_bd - timedelta(days=1)).date().isoformat()


def get_business_date_from_datetime(dt: datetime) -> str:
    """
    Convert any datetime object to its corresponding business date string (YYYY-MM-DD).
    """
    if dt.hour < BUSINESS_DAY_START_HOUR:
        return (dt.date() - timedelta(days=1)).isoformat()
    return dt.date().isoformat()


def get_business_date_range(date_str: str) -> tuple[str, str]:
    """
    Get the start and end datetime strings for a specific business date.
    
    Args:
        date_str: Business date in 'YYYY-MM-DD' format
        
    Returns:
        tuple (start_str, end_str) in 'YYYY-MM-DD HH:MM:SS' format
        
    Example:
        Input: '2026-01-28'
        Output: ('2026-01-28 05:00:00', '2026-01-29 04:59:59')
    """
    base = datetime.fromisoformat(date_str)
    
    # Start: 5:00 AM on the business date
    start = base.replace(hour=BUSINESS_DAY_START_HOUR, minute=0, second=0, microsecond=0)
    
    # End: 4:59:59 AM on the NEXT day
    # calculated as Start + 1 Day - 1 Second
    end = start + timedelta(days=1) - timedelta(seconds=1)
    
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')
