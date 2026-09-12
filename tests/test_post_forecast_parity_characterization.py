"""Small, independent characterization gates for post-Forecast R3 review.

These tests deliberately compare the current production core with the alternate
package without making the alternate result a source of truth.
"""

from datetime import date

import pandas as pd
import pytest

from shelfcash_core.bom.engine import propagate_ingredient_demand as core_bom
from shelfcash_core.contracts import ForecastPackage as CoreForecastPackage
from shelfcash_core.contracts import ForecastPrediction as CoreForecastPrediction
from shelfcash_core.inventory.contracts import InventoryLot as CoreLot
from shelfcash_core.inventory.contracts import InventorySimulationPolicy as CorePolicy
from shelfcash_core.inventory.fefo import consume_fefo as core_consume_fefo
from shelfcash_forecast.bom.engine import propagate_ingredient_demand as shadow_bom
from shelfcash_forecast.contracts import ForecastPackage as ShadowForecastPackage
from shelfcash_forecast.contracts import ForecastPrediction as ShadowForecastPrediction
from shelfcash_forecast.inventory.contracts import InventoryLot as ShadowLot
from shelfcash_forecast.inventory.contracts import InventorySimulationPolicy as ShadowPolicy
from shelfcash_forecast.inventory.fefo import consume_fefo as shadow_consume_fefo
from shelfcash_core.exceptions import UnitConversionError as CoreUnitConversionError
from shelfcash_forecast.exceptions import UnitConversionError as ShadowUnitConversionError


def _forecast(package_type, prediction_type):
    return package_type(
        forecast_date=date(2026, 8, 10),
        forecast_horizon=1,
        model_version="r3-characterization",
        predictions=[
            prediction_type(
                store_id="store", product_id="product", product_name="Product",
                unit="unit", target_date=date(2026, 8, 11), horizon=1,
                p25=10, p50=20, p75=30, interval_lower=8, interval_upper=32,
                baseline_p50=20, calibration_source="test", warnings=["FORECAST_WARNING"],
            )
        ],
    )


def _recipes(*, lines=None):
    return pd.DataFrame(lines if lines is not None else [
        {
            "recipe_line_id": "line-1", "recipe_id": "recipe-1", "recipe_version": "1",
            "product_id": "product", "ingredient_id": "ingredient", "ingredient_name": "Ingredient",
            "ingredient_quantity": 0.5, "ingredient_unit": "kg", "yield_quantity": 1,
            "yield_unit": "unit", "process_loss_rate": 0.1, "waste_allowance_rate": 0.0,
            "effective_from": date(2026, 1, 1), "effective_to": None,
        }
    ])


def test_bom_normal_recipe_has_full_contribution_provenance_parity():
    """The shadow preserves the actual source primary key; it does not invent one."""

    core = core_bom(_forecast(CoreForecastPackage, CoreForecastPrediction), _recipes())
    shadow = shadow_bom(_forecast(ShadowForecastPackage, ShadowForecastPrediction), _recipes())

    core_prediction = core.predictions[0]
    shadow_prediction = shadow.predictions[0]
    assert (core_prediction.target_date, core_prediction.unit, core_prediction.p25, core_prediction.p50, core_prediction.p75) == (
        shadow_prediction.target_date, shadow_prediction.unit, shadow_prediction.p25, shadow_prediction.p50, shadow_prediction.p75
    )
    assert core_prediction.warnings == shadow_prediction.warnings == ["FORECAST_WARNING"]
    assert core_prediction.sources[0].model_dump(mode="json") == shadow_prediction.sources[0].model_dump(mode="json")
    assert core_prediction.sources[0].recipe_line_id == shadow_prediction.sources[0].recipe_line_id == "line-1"


def test_bom_duplicate_recipe_lines_keep_distinct_truthful_source_ids():
    base = _recipes().iloc[0].to_dict()
    lines = [
        {**base, "recipe_line_id": "line-a", "ingredient_quantity": 0.25},
        {**base, "recipe_line_id": "line-b", "ingredient_quantity": 0.75},
    ]
    core = core_bom(_forecast(CoreForecastPackage, CoreForecastPrediction), _recipes(lines=lines))
    shadow = shadow_bom(_forecast(ShadowForecastPackage, ShadowForecastPrediction), _recipes(lines=lines))

    core_prediction = core.predictions[0]
    shadow_prediction = shadow.predictions[0]
    assert core_prediction.model_dump(mode="json") == shadow_prediction.model_dump(mode="json")
    assert [source.recipe_line_id for source in core_prediction.sources] == ["line-a", "line-b"]
    assert len(core_prediction.sources) == 2


def test_bom_missing_recipe_has_matching_issue_without_a_contribution():
    other_recipe_frame = _recipes(lines=[{**_recipes().iloc[0].to_dict(), "product_id": "other-product"}])
    core = core_bom(_forecast(CoreForecastPackage, CoreForecastPrediction), other_recipe_frame)
    shadow = shadow_bom(_forecast(ShadowForecastPackage, ShadowForecastPrediction), other_recipe_frame)

    assert core.model_dump(mode="json") == shadow.model_dump(mode="json")
    assert core.predictions == []
    assert core.issues[0].code == "MISSING_RECIPE"


def test_bom_incompatible_product_and_yield_units_fail_equivalently():
    forecast_core = _forecast(CoreForecastPackage, CoreForecastPrediction)
    forecast_shadow = _forecast(ShadowForecastPackage, ShadowForecastPrediction)
    forecast_core.predictions[0].unit = "kg"
    forecast_shadow.predictions[0].unit = "kg"
    lines = [{**_recipes().iloc[0].to_dict(), "yield_unit": "liter"}]

    with pytest.raises(CoreUnitConversionError) as core_error:
        core_bom(forecast_core, _recipes(lines=lines))
    with pytest.raises(ShadowUnitConversionError) as shadow_error:
        shadow_bom(forecast_shadow, _recipes(lines=lines))

    assert core_error.value.to_dict() == shadow_error.value.to_dict()


def test_fefo_known_expiry_consumption_matches_for_the_shared_happy_path():
    core = core_consume_fefo(
        [CoreLot(lot_id="early", store_id="store", ingredient_id="ingredient", quantity_remaining=2, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 12)),
         CoreLot(lot_id="late", store_id="store", ingredient_id="ingredient", quantity_remaining=3, unit="kg", received_date=date(2026, 8, 2), expiry_date=date(2026, 8, 14))],
        4, scenario_id="scenario", simulation_date=date(2026, 8, 11), policy=CorePolicy(),
    )
    shadow = shadow_consume_fefo(
        [ShadowLot(lot_id="early", store_id="store", ingredient_id="ingredient", quantity_remaining=2, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 12)),
         ShadowLot(lot_id="late", store_id="store", ingredient_id="ingredient", quantity_remaining=3, unit="kg", received_date=date(2026, 8, 2), expiry_date=date(2026, 8, 14))],
        4, scenario_id="scenario", simulation_date=date(2026, 8, 11), policy=ShadowPolicy(),
    )
    assert (core.fulfilled_quantity, core.shortage_quantity, [x.lot_id for x in core.traces], core.warnings) == (
        shadow.fulfilled_quantity, shadow.shortage_quantity, [x.lot_id for x in shadow.traces], shadow.warnings
    ) == (4, 0, ["early", "late"], [])


def test_fefo_not_required_expiry_semantics_are_intentionally_not_parity_equivalent():
    """Core's non-perishable FIFO mode must not be collapsed into shadow's unknown-expiry warning."""

    core = core_consume_fefo(
        [CoreLot(lot_id="non-perishable", store_id="store", ingredient_id="ingredient", quantity_remaining=1, unit="kg", expiry_tracking_mode="not_required")],
        1, scenario_id="scenario", simulation_date=date(2026, 8, 11), policy=CorePolicy(unknown_expiry="warn_and_place_last"),
    )
    shadow = shadow_consume_fefo(
        [ShadowLot(lot_id="no-expiry", store_id="store", ingredient_id="ingredient", quantity_remaining=1, unit="kg", received_date=date(2026, 8, 1))],
        1, scenario_id="scenario", simulation_date=date(2026, 8, 11), policy=ShadowPolicy(unknown_expiry="warn_and_place_last"),
    )
    assert core.warnings == []
    assert shadow.warnings == ["UNKNOWN_EXPIRY_PLACED_LAST"]
