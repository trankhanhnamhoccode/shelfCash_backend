from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.exceptions import DataValidationError, FeatureTypeError


@dataclass(frozen=True)
class ForecastInput:
    sales_history: pd.DataFrame
    calendar_features: pd.DataFrame | None
    inventory_availability: pd.DataFrame | None


def normalize_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    text = unicodedata.normalize("NFKD", text.strip().lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def stable_entity_key(prefix: str, value: object) -> str:
    normalized = normalize_text(value)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _coerce_nullable_bool(series: pd.Series) -> pd.Series:
    if str(series.dtype) == "boolean":
        return series

    truthy = {"1", "true", "yes", "y", "co", "có", "x"}
    falsy = {"0", "false", "no", "n", "khong", "không"}

    def convert(value: object) -> object:
        if pd.isna(value):
            return pd.NA
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False
        normalized = normalize_text(value)
        if normalized in truthy:
            return True
        if normalized in falsy:
            return False
        return pd.NA

    return series.map(convert).astype("boolean")


def _coerce_numeric(series: pd.Series, column: str) -> pd.Series:
    converted = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & converted.isna()
    if invalid.any():
        examples = series.loc[invalid].astype(str).drop_duplicates().head(3).tolist()
        raise FeatureTypeError(f"{column} contains non-numeric values: {examples}")
    return converted.astype("Float64")


def adapt_sales_history(
    sales: pd.DataFrame,
    config: ForecastConfig,
) -> pd.DataFrame:
    required = {"date", "product_name", "quantity_sold"}
    missing = required - set(sales.columns)
    if missing:
        raise DataValidationError(
            f"sales_history thiếu cột bắt buộc: {sorted(missing)}"
        )

    frame = sales.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["product_name"] = frame["product_name"].astype("string").str.strip()
    frame["quantity_sold"] = _coerce_numeric(frame["quantity_sold"], "quantity_sold")

    if "store_id" in frame.columns:
        store = frame["store_id"].astype("string").str.strip()
        frame["store_key"] = store.fillna(config.default_store_key)
        frame.loc[frame["store_key"].eq(""), "store_key"] = config.default_store_key
    else:
        frame["store_key"] = config.default_store_key

    if "product_id" in frame.columns:
        product_id = frame["product_id"].astype("string").str.strip()
        generated = frame["product_name"].map(lambda value: stable_entity_key("PRD", value))
        frame["product_key"] = product_id.where(product_id.notna() & product_id.ne(""), generated)
    else:
        frame["product_key"] = frame["product_name"].map(
            lambda value: stable_entity_key("PRD", value)
        )

    if "is_stockout" not in frame.columns:
        frame["is_stockout"] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    else:
        frame["is_stockout"] = _coerce_nullable_bool(frame["is_stockout"])

    if "selling_price" not in frame.columns:
        frame["selling_price"] = pd.NA
    frame["selling_price"] = _coerce_numeric(frame["selling_price"], "selling_price")

    if "revenue" not in frame.columns:
        frame["revenue"] = pd.NA
    frame["revenue"] = _coerce_numeric(frame["revenue"], "revenue")

    if "unit" not in frame.columns:
        frame["unit"] = pd.NA
    frame["unit"] = frame["unit"].astype("string")

    if "promotion_name" not in frame.columns:
        frame["promotion_name"] = pd.NA
    frame["promotion_name"] = frame["promotion_name"].astype("string")

    columns = [
        "date",
        "store_key",
        "product_key",
        "product_name",
        "quantity_sold",
        "unit",
        "selling_price",
        "revenue",
        "is_stockout",
        "promotion_name",
    ]
    return frame[columns]


def adapt_calendar(calendar: pd.DataFrame | None) -> pd.DataFrame | None:
    if calendar is None or calendar.empty:
        return None
    if "date" not in calendar.columns:
        raise DataValidationError("calendar_features thiếu cột date.")

    frame = calendar.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()

    for column in ("is_weekend", "is_holiday", "is_store_closed", "is_promotion"):
        if column not in frame.columns:
            frame[column] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
        else:
            frame[column] = _coerce_nullable_bool(frame[column])

    for column in ("temperature", "rainfall", "planned_price", "discount_rate"):
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = _coerce_numeric(frame[column], column)

    if "promotion_name" not in frame.columns:
        frame["promotion_name"] = pd.NA
    frame["promotion_name"] = frame["promotion_name"].astype("string")

    for column in ("promotion_type", "promotion_category", "calendar_event"):
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = frame[column].astype("string")
    if "known_at" not in frame.columns:
        frame["known_at"] = pd.NaT
    frame["known_at"] = pd.to_datetime(frame["known_at"], errors="coerce").dt.tz_localize(None)

    return frame[
        [
            "date",
            "is_weekend",
            "is_holiday",
            "is_store_closed",
            "is_promotion",
            "promotion_name",
            "temperature",
            "rainfall",
            "planned_price",
            "discount_rate",
            "promotion_type",
            "promotion_category",
            "calendar_event",
            "known_at",
        ]
    ]


def adapt_inventory_availability(data: pd.DataFrame | None, config: ForecastConfig) -> pd.DataFrame | None:
    if data is None or data.empty:
        return None
    required = {"date"}
    if not required.issubset(data.columns):
        raise DataValidationError("inventory_availability requires date")
    frame = data.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["store_key"] = frame.get("store_id", config.default_store_key)
    if "product_id" in frame.columns:
        frame["product_key"] = frame["product_id"].astype("string")
    elif "product_name" in frame.columns:
        frame["product_key"] = frame["product_name"].map(lambda value: stable_entity_key("PRD", value))
    else:
        raise DataValidationError("inventory_availability requires product_id or product_name")
    if "is_available" in frame.columns:
        frame["is_available"] = _coerce_nullable_bool(frame["is_available"])
    elif "available_quantity" in frame.columns:
        quantity = _coerce_numeric(frame["available_quantity"], "available_quantity")
        frame["is_available"] = quantity.gt(0).astype("boolean")
    else:
        raise DataValidationError("inventory_availability requires is_available or available_quantity")
    return frame[["date", "store_key", "product_key", "is_available"]]


def adapt_forecast_input(
    canonical_data: Mapping[str, pd.DataFrame],
    config: ForecastConfig,
) -> ForecastInput:
    if "sales_history" not in canonical_data:
        raise DataValidationError("canonical_data bắt buộc có sales_history.")

    return ForecastInput(
        sales_history=adapt_sales_history(canonical_data["sales_history"], config),
        calendar_features=adapt_calendar(canonical_data.get("calendar_features")),
        inventory_availability=adapt_inventory_availability(canonical_data.get("inventory_availability"), config),
    )
