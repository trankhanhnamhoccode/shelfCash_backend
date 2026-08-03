from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from shelfcash_forecast.exceptions import DataValidationError


@dataclass
class DataQualityReport:
    rows_received: int
    rows_valid: int = 0
    duplicate_rows: int = 0
    invalid_date_rows: int = 0
    missing_product_rows: int = 0
    missing_quantity_rows: int = 0
    negative_sales_rows: int = 0
    missing_stockout_rows: int = 0
    stockout_information_missing: bool = False
    calendar_rows_received: int = 0
    calendar_invalid_date_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _nullable_bool_any(series: pd.Series) -> object:
    observed = series.dropna()
    if observed.empty:
        return pd.NA
    return bool(observed.astype(bool).any())


def validate_sales(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, DataQualityReport]:
    report = DataQualityReport(rows_received=len(frame))
    clean = frame.copy()

    report.invalid_date_rows = int(clean["date"].isna().sum())
    missing_product_mask = clean["product_name"].isna() | clean["product_name"].eq("")
    report.missing_product_rows = int(missing_product_mask.sum())
    report.missing_quantity_rows = int(clean["quantity_sold"].isna().sum())
    report.negative_sales_rows = int(clean["quantity_sold"].lt(0).fillna(False).sum())
    report.missing_stockout_rows = int(clean["is_stockout"].isna().sum())
    report.stockout_information_missing = bool(clean["is_stockout"].isna().all())

    fatal_mask = (
        clean["date"].isna()
        | clean["product_name"].isna()
        | clean["product_name"].eq("")
        | clean["quantity_sold"].isna()
        | clean["quantity_sold"].lt(0).fillna(False)
    )
    clean = clean.loc[~fatal_mask].copy()

    key = ["date", "store_key", "product_key"]
    duplicate_mask = clean.duplicated(key, keep=False)
    report.duplicate_rows = int(duplicate_mask.sum())

    if report.duplicate_rows:
        clean = (
            clean.groupby(key, as_index=False, observed=True)
            .agg(
                product_name=("product_name", "first"),
                quantity_sold=("quantity_sold", "sum"),
                unit=("unit", "first"),
                selling_price=("selling_price", "mean"),
                revenue=("revenue", "sum"),
                is_stockout=("is_stockout", _nullable_bool_any),
                promotion_name=("promotion_name", "first"),
            )
            .sort_values(key)
            .reset_index(drop=True)
        )
        clean["is_stockout"] = clean["is_stockout"].astype("boolean")
        report.warnings.append("DUPLICATE_DAILY_ROWS_AGGREGATED")

    if report.stockout_information_missing:
        report.warnings.append("STOCKOUT_INFORMATION_MISSING")
    if report.invalid_date_rows:
        report.warnings.append("INVALID_DATE_ROWS_DROPPED")
    if report.negative_sales_rows:
        report.warnings.append("NEGATIVE_SALES_ROWS_DROPPED")

    report.rows_valid = len(clean)
    if clean.empty:
        raise DataValidationError("Không còn dòng sales_history hợp lệ sau validation.")

    return clean.sort_values(key).reset_index(drop=True), report


def validate_calendar(
    calendar: pd.DataFrame | None,
    report: DataQualityReport,
) -> pd.DataFrame | None:
    if calendar is None:
        report.warnings.append("CALENDAR_FEATURES_MISSING")
        return None

    clean = calendar.copy()
    report.calendar_rows_received = len(clean)
    report.calendar_invalid_date_rows = int(clean["date"].isna().sum())
    clean = clean.loc[clean["date"].notna()].copy()

    if clean.duplicated(["date"]).any():
        clean = (
            clean.groupby("date", as_index=False)
            .agg(
                is_weekend=("is_weekend", _nullable_bool_any),
                is_holiday=("is_holiday", _nullable_bool_any),
                is_store_closed=("is_store_closed", _nullable_bool_any),
                is_promotion=("is_promotion", _nullable_bool_any),
                promotion_name=("promotion_name", "first"),
                temperature=("temperature", "mean"),
                rainfall=("rainfall", "mean"),
            )
        )
        for column in ("is_weekend", "is_holiday", "is_store_closed", "is_promotion"):
            clean[column] = clean[column].astype("boolean")
        report.warnings.append("DUPLICATE_CALENDAR_ROWS_AGGREGATED")

    return clean.sort_values("date").reset_index(drop=True)
