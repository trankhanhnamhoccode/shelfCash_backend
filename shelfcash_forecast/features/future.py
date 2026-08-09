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
    *,
    allow_unversioned_context: bool = False,
) -> pd.DataFrame:
    result = frame.copy()

    if calendar is None or calendar.empty:
        result["target_is_holiday"] = 0
        result["target_store_closed"] = 0
        result["target_temperature"] = pd.NA
        result["target_rainfall"] = pd.NA
        result["target_planned_price"] = pd.NA
        result["target_discount_rate"] = pd.NA
        result["target_is_promotion"] = 0
        result["target_promotion_known"] = 0
        result["target_promotion_category"] = "__MISSING__"
        result["target_calendar_event"] = 0
        result["effective_price"] = result["last_observed_price"]
        result["price_change"] = 0.0
        result["calendar_available"] = 0
        return result

    calendar_future = calendar.rename(
        columns={
            "date": "target_date",
            "is_holiday": "target_is_holiday",
            "is_store_closed": "target_store_closed",
            "temperature": "target_temperature",
            "rainfall": "target_rainfall",
            "planned_price": "target_planned_price",
            "discount_rate": "target_discount_rate",
            "is_promotion": "target_is_promotion",
            "promotion_category": "target_promotion_category",
            "promotion_type": "target_promotion_type",
            "calendar_event": "target_calendar_event_name",
        }
    )
    selected = [
        "target_date",
        "target_is_holiday",
        "target_store_closed",
        "target_temperature",
        "target_rainfall",
        "target_planned_price",
        "target_discount_rate",
        "target_is_promotion",
        "target_promotion_category",
        "target_promotion_type",
        "target_calendar_event_name",
        "promotion_name",
        "known_at",
    ]
    result = result.merge(
        calendar_future[selected],
        on="target_date",
        how="left",
        validate="many_to_one",
    )
    # A contextual value is leakage-safe only when it was recorded no later than
    # the row's prediction cutoff. Inference may explicitly accept an unversioned
    # plan because that frame is present at prediction time; training never does.
    known = result["known_at"].le(result["cutoff_date"])
    if allow_unversioned_context:
        known = known | result["known_at"].isna()
    contextual = [
        "target_planned_price", "target_discount_rate", "target_is_promotion",
        "target_promotion_category", "target_promotion_type",
        "target_calendar_event_name", "promotion_name",
        "target_temperature", "target_rainfall",
    ]
    for column in contextual:
        result[column] = result[column].astype("object").where(known, pd.NA)

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
    result["target_planned_price"] = pd.to_numeric(result["target_planned_price"], errors="coerce")
    result["target_discount_rate"] = pd.to_numeric(result["target_discount_rate"], errors="coerce")
    result["target_promotion_known"] = result["target_is_promotion"].notna().astype("int8")
    result["target_is_promotion"] = result["target_is_promotion"].fillna(False).astype("int8")
    category = result["target_promotion_category"].fillna(result["target_promotion_type"])
    result["target_promotion_category"] = category.fillna(result["promotion_name"]).fillna("__MISSING__").astype("string")
    result["target_calendar_event"] = result["target_calendar_event_name"].notna().astype("int8")
    historical_price = pd.to_numeric(result["last_observed_price"], errors="coerce")
    result["effective_price"] = result["target_planned_price"].fillna(historical_price)
    result["price_change"] = result["effective_price"] - historical_price
    return result
