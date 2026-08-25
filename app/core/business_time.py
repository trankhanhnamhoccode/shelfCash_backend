"""Small, explicit business-date helpers for planning chronology.

``cutoff_date`` is an EOD snapshot boundary.  It is deliberately distinct
from the first date on which a new plan may consume demand or place an order.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def planning_start_date(cutoff_date: date) -> date:
    """Return the first local business day after an EOD cutoff."""
    return cutoff_date + timedelta(days=1)


def planning_end_date(cutoff_date: date, horizon_days: int) -> date:
    if horizon_days < 1:
        raise ValueError("horizon_days must be positive")
    return planning_start_date(cutoff_date) + timedelta(days=horizon_days - 1)


def local_business_date(value: datetime, timezone_name: str) -> date:
    """Convert an aware instant to the store's local business date."""
    if value.tzinfo is None:
        # SQLite does not round-trip timezone offsets.  Legacy naive values
        # were written as UTC, so interpret them consistently as UTC.
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo(timezone_name)).date()


def snapshot_eod_boundary(snapshot_date: date, timezone_name: str) -> datetime:
    """Exclusive EOD boundary for a date-only local inventory observation.

    It is the next local midnight represented as an aware UTC instant.  Code
    that needs the observation's business date must use the stored snapshot
    source date, rather than inferring it from this exclusive boundary.
    """
    local_boundary = datetime.combine(
        snapshot_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)
    )
    return local_boundary.astimezone(timezone.utc)
