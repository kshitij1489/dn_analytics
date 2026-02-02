"""
Business Date Utilities

The cafe operates until 5:00 AM IST, so a "business day" runs from
05:00:00 on Day 1 to 04:59:59 on Day 2 (IST).

All analytics should use these utilities for consistent date handling.
"""
from datetime import datetime, date, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BUSINESS_DAY_START_HOUR = 5  # 5:00 AM IST

# SQL fragment for SQLite to calculate business date
# Subtracts 5 hours from IST timestamp to align with business day
# e.g., '2023-01-01 02:00:00' -> '2022-12-31 21:00:00' -> DATE(...) -> '2022-12-31'
BUSINESS_DATE_SQL = "DATE(created_on, '-5 hours')"


def get_current_business_date() -> str:
    """
    Get the current business date in YYYY-MM-DD format (IST).
    
    If current time in IST is before 5 AM, returns yesterday's date.
    Else returns today's date.
    """
    now = datetime.now(IST)
    if now.hour < BUSINESS_DAY_START_HOUR:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


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
