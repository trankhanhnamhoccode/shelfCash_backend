from datetime import date, datetime, timezone

from app.core.business_time import local_business_date, planning_end_date, planning_start_date


def test_eod_cutoff_planning_dates_are_exact_and_timezone_safe():
    cutoff = date(2026, 8, 20)
    assert planning_start_date(cutoff) == date(2026, 8, 21)
    assert planning_end_date(cutoff, 7) == date(2026, 8, 27)
    # 18:30 UTC is already the next local day in the store's UTC+7 zone.
    assert local_business_date(datetime(2026, 8, 20, 18, 30, tzinfo=timezone.utc), "Asia/Ho_Chi_Minh") == date(2026, 8, 21)
