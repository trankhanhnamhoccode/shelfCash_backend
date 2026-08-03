from __future__ import annotations

import pandas as pd


def build_daily_panel(
    sales: pd.DataFrame,
    calendar: pd.DataFrame | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Create one row per day and observed store-product series."""

    series = sales[
        ["store_key", "product_key", "product_name", "unit"]
    ].drop_duplicates(["store_key", "product_key"])

    panel_end = sales["date"].max()
    if end_date is not None:
        panel_end = max(panel_end, pd.Timestamp(end_date).normalize())

    date_frame = pd.DataFrame(
        {
            "date": pd.date_range(
                sales["date"].min(),
                panel_end,
                freq="D",
            )
        }
    )

    panel = (
        series.assign(_join_key=1)
        .merge(date_frame.assign(_join_key=1), on="_join_key", how="inner")
        .drop(columns="_join_key")
    )

    observed_columns = [
        "date",
        "store_key",
        "product_key",
        "quantity_sold",
        "selling_price",
        "revenue",
        "is_stockout",
        "promotion_name",
    ]
    panel = panel.merge(
        sales[observed_columns],
        on=["date", "store_key", "product_key"],
        how="left",
        validate="one_to_one",
    )
    panel["row_observed"] = panel["quantity_sold"].notna()

    if calendar is not None and not calendar.empty:
        panel = panel.merge(calendar, on="date", how="left", validate="many_to_one")

    if "is_store_closed" not in panel.columns:
        panel["is_store_closed"] = False
    panel["is_store_closed"] = panel["is_store_closed"].fillna(False).astype(bool)
    panel["store_open"] = ~panel["is_store_closed"]

    if "is_holiday" not in panel.columns:
        panel["is_holiday"] = False
    if "temperature" not in panel.columns:
        panel["temperature"] = pd.NA
    if "rainfall" not in panel.columns:
        panel["rainfall"] = pd.NA

    return panel.sort_values(
        ["store_key", "product_key", "date"]
    ).reset_index(drop=True)


def resolve_missing_sales(panel: pd.DataFrame) -> pd.DataFrame:
    frame = panel.copy()
    frame["sales_missing_reason"] = pd.Series(pd.NA, index=frame.index, dtype="string")

    closed = ~frame["store_open"]
    frame.loc[closed, "quantity_sold"] = pd.NA
    frame.loc[closed & ~frame["row_observed"], "sales_missing_reason"] = "STORE_CLOSED"

    missing_open = frame["store_open"] & ~frame["row_observed"]
    frame.loc[missing_open, "sales_missing_reason"] = "MISSING_OPEN_DAY_RECORD"

    return frame
