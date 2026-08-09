from decimal import Decimal

import pandas as pd
import pytest

from app.core.exceptions import ForecastError
from app.services.forecast_service import ForecastService
from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.data.adapter import adapt_forecast_input
from shelfcash_forecast.data.demand_reconstruction import reconstruct_demand
from shelfcash_forecast.exceptions import FeatureTypeError
from shelfcash_forecast.features.future import add_calendar_future_features
from shelfcash_forecast.features.specification import NUMERIC_FEATURES, normalize_model_numeric_features


def _future_row() -> pd.DataFrame:
    return pd.DataFrame([{
        "cutoff_date": pd.Timestamp("2026-08-05"),
        "target_date": pd.Timestamp("2026-08-06"),
        "last_observed_price": Decimal("10.5"),
    }])


def test_decimal_and_string_numeric_normalization():
    row = {column: Decimal("1.5") for column in NUMERIC_FEATURES}
    row["target_temperature"] = "31.25"
    normalized = normalize_model_numeric_features(pd.DataFrame([row]))
    assert all(normalized[column].dtype == "float64" for column in NUMERIC_FEATURES)
    assert normalized.iloc[0]["target_temperature"] == 31.25


def test_invalid_numeric_has_specific_domain_error():
    row = {column: 1 for column in NUMERIC_FEATURES}
    row["target_rainfall"] = "not-a-number"
    with pytest.raises(FeatureTypeError):
        normalize_model_numeric_features(pd.DataFrame([row]))
    service = object.__new__(ForecastService)
    with pytest.raises(ForecastError) as raised:
        service._raise_core(FeatureTypeError("bad rainfall"), training=True)
    assert raised.value.code == "FORECAST_FEATURE_TYPE_INVALID"


def test_known_future_promotion_and_planned_price_are_used():
    calendar = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-06"), "is_holiday": False,
        "is_store_closed": False, "temperature": "30", "rainfall": Decimal("2.5"),
        "planned_price": "12.00", "discount_rate": "0.10", "is_promotion": True,
        "promotion_name": "Launch", "promotion_type": "campaign",
        "promotion_category": "seasonal", "calendar_event": "summer",
        "known_at": pd.Timestamp("2026-08-01"),
    }])
    result = add_calendar_future_features(_future_row(), calendar)
    assert result.iloc[0]["target_is_promotion"] == 1
    assert result.iloc[0]["target_promotion_category"] == "seasonal"
    assert result.iloc[0]["target_planned_price"] == 12
    assert result.iloc[0]["effective_price"] == 12
    assert result.iloc[0]["price_change"] == 1.5


def test_context_not_known_at_cutoff_is_masked_and_revenue_never_becomes_feature():
    calendar = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-06"), "is_holiday": False,
        "is_store_closed": False, "temperature": 30, "rainfall": 0,
        "planned_price": 20, "discount_rate": .5, "is_promotion": True,
        "promotion_name": "realized", "promotion_type": "flash",
        "promotion_category": "flash", "calendar_event": pd.NA,
        "known_at": pd.Timestamp("2026-08-07"),
    }])
    result = add_calendar_future_features(_future_row(), calendar)
    assert result.iloc[0]["target_is_promotion"] == 0
    assert pd.isna(result.iloc[0]["target_planned_price"])
    assert result.iloc[0]["effective_price"] == 10.5
    assert "revenue" not in NUMERIC_FEATURES


def test_stockout_reconstructs_only_with_sufficient_prior_evidence():
    dates = pd.date_range("2026-08-01", periods=6)
    panel = pd.DataFrame({
        "date": dates, "store_key": "s", "product_key": "p", "store_open": True,
        "row_observed": True, "quantity_sold": [10, 12, 11, 13, 2, 1],
        "is_stockout": pd.Series([False, False, False, False, True, pd.NA], dtype="boolean"),
        "is_available": pd.Series([pd.NA, pd.NA, pd.NA, pd.NA, pd.NA, False], dtype="boolean"),
    })
    result = reconstruct_demand(panel)
    assert result.iloc[4]["demand_proxy"] == 11.5
    assert result.iloc[4]["target_quality"] == "stockout_reconstructed"
    assert result.iloc[5]["stockout_reconstruction_source"] == "inventory_historical_median"


def test_missing_inventory_and_stockout_falls_back_without_lost_demand():
    panel = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-01"), "store_key": "s", "product_key": "p",
        "store_open": True, "row_observed": True, "quantity_sold": 2,
        "is_stockout": pd.NA, "is_available": pd.NA,
    }])
    result = reconstruct_demand(panel)
    assert result.iloc[0]["demand_proxy"] == 2
    assert result.iloc[0]["target_quality"] == "stockout_unknown"
    assert result.iloc[0]["stockout_reconstruction_confidence"] == 0


def test_adapter_normalizes_context_and_never_requires_realized_revenue():
    adapted = adapt_forecast_input({
        "sales_history": pd.DataFrame([{
            "date": "2026-08-01", "product_name": "Coffee", "quantity_sold": Decimal("2"),
            "selling_price": "10.5", "revenue": "21.0",
        }]),
        "calendar_features": pd.DataFrame([{"date": "2026-08-02", "temperature": "30", "rainfall": Decimal("1.2")}]),
    }, ForecastConfig())
    assert adapted.sales_history["selling_price"].dtype.name == "Float64"
    assert adapted.calendar_features["temperature"].dtype.name == "Float64"
