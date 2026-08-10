from __future__ import annotations

import pandas as pd


def add_deterministic_future_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    target = pd.to_datetime(result["target_date"])

    result["target_day_of_week"] = target.dt.dayofweek.astype("int8")
    result["target_is_weekend"] = result["target_day_of_week"].ge(5).astype("int8")
    result["target_month"] = target.dt.month.astype("int8")
    result["target_day_of_month"] = target.dt.day.astype("int8")
    result["target_week_of_month"] = (
        ((result["target_day_of_month"] - 1) // 7) + 1
    ).astype("int8")
    result["target_week_of_year"] = target.dt.isocalendar().week.astype("int16")
    return result


def add_calendar_future_features(
    frame: pd.DataFrame,
    calendar: pd.DataFrame | None,
) -> pd.DataFrame:
    result = frame.copy()

    if calendar is None or calendar.empty:
        result["target_is_holiday"] = 0
        result["target_store_closed"] = 0
        # Keep optional numeric columns numeric.  ``pd.NA`` alone creates an
        # object column which LightGBM correctly refuses to consume.
        result["target_temperature"] = float("nan")
        result["target_rainfall"] = float("nan")
        result["calendar_available"] = 0
        return result

    calendar_future = calendar.rename(
        columns={
            "date": "target_date",
            "is_holiday": "target_is_holiday",
            "is_store_closed": "target_store_closed",
            "temperature": "target_temperature",
            "rainfall": "target_rainfall",
        }
    )
    selected = [
        "target_date",
        "target_is_holiday",
        "target_store_closed",
        "target_temperature",
        "target_rainfall",
    ]
    result = result.merge(
        calendar_future[selected],
        on="target_date",
        how="left",
        validate="many_to_one",
    )

    result["calendar_available"] = (
        result["target_is_holiday"].notna()
        | result["target_store_closed"].notna()
        | result["target_temperature"].notna()
        | result["target_rainfall"].notna()
    ).astype("int8")
    result["target_is_holiday"] = (
        result["target_is_holiday"].fillna(False).astype("int8")
    )
    result["target_store_closed"] = (
        result["target_store_closed"].fillna(False).astype("int8")
    )
    result["target_temperature"] = pd.to_numeric(
        result["target_temperature"], errors="coerce"
    )
    result["target_rainfall"] = pd.to_numeric(
        result["target_rainfall"], errors="coerce"
    )
    return result
