from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.exceptions import DataValidationError


@dataclass(frozen=True)
class ForecastInput:
    sales_history: pd.DataFrame
    calendar_features: pd.DataFrame | None


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
    frame["quantity_sold"] = pd.to_numeric(frame["quantity_sold"], errors="coerce")

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
    frame["selling_price"] = pd.to_numeric(frame["selling_price"], errors="coerce")

    if "revenue" not in frame.columns:
        frame["revenue"] = pd.NA
    frame["revenue"] = pd.to_numeric(frame["revenue"], errors="coerce")

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

    for column in ("temperature", "rainfall"):
        if column not in frame.columns:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    if "promotion_name" not in frame.columns:
        frame["promotion_name"] = pd.NA
    frame["promotion_name"] = frame["promotion_name"].astype("string")

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
        ]
    ]


def adapt_forecast_input(
    canonical_data: Mapping[str, pd.DataFrame],
    config: ForecastConfig,
) -> ForecastInput:
    if "sales_history" not in canonical_data:
        raise DataValidationError("canonical_data bắt buộc có sales_history.")

    return ForecastInput(
        sales_history=adapt_sales_history(canonical_data["sales_history"], config),
        calendar_features=adapt_calendar(canonical_data.get("calendar_features")),
    )
