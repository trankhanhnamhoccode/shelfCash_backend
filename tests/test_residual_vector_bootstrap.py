from datetime import date, timedelta

import pandas as pd
import pytest

from shelfcash_core.contracts import ForecastPackage, ForecastPrediction
from shelfcash_core.inventory.contracts import (
    InventoryDemandLine,
    InventoryDemandScenario,
)
from shelfcash_core.scenario.bootstrap import (
    MAX_UNIQUE_COHERENT_BLOCK_POOL,
    ResidualVectorBootstrapScenarioGenerator,
)
from shelfcash_core.scenario.sufficiency import select_stochastic_scenarios


STORE = "STORE_001"
FORECAST_DATE = date(2026, 8, 1)


def _forecast(*, products: int = 5, horizon: int = 7) -> ForecastPackage:
    return ForecastPackage(
        forecast_date=FORECAST_DATE,
        forecast_horizon=horizon,
        model_version="test-v1",
        predictions=[
            ForecastPrediction(
                store_id=STORE,
                product_id=f"product-{product}",
                product_name=f"Product {product}",
                unit="each",
                target_date=FORECAST_DATE + timedelta(days=step),
                horizon=step,
                p25=9.0,
                p50=10.0,
                p75=11.0,
                interval_lower=9.0,
                interval_upper=11.0,
                baseline_p50=10.0,
                calibration_source="test",
            )
            for product in range(products)
            for step in range(1, horizon + 1)
        ],
    )


def _residuals(
    block_count: int,
    *,
    products: int = 5,
    horizon: int = 7,
    scaled_values: list[float] | None = None,
) -> pd.DataFrame:
    rows = []
    for block_index in range(block_count):
        origin = FORECAST_DATE - timedelta(days=block_count - block_index + 7)
        scaled = (
            scaled_values[block_index]
            if scaled_values is not None
            else float(block_index + 1)
        )
        for product in range(products):
            for step in range(1, horizon + 1):
                rows.append({
                    "forecast_origin": origin,
                    "target_date": origin + timedelta(days=step),
                    "horizon": step,
                    "store_id": STORE,
                    "product_id": f"product-{product}",
                    "actual": max(0.0, 10.0 + scaled * 2.0),
                    "p25": 9.0,
                    "p50": 10.0,
                    "p75": 11.0,
                    "raw_residual": scaled * 2.0,
                    "scaled_residual": scaled,
                    "target_train_eligible": True,
                    "residual_source": "walk_forward_oos",
                })
    return pd.DataFrame(rows)


def _generate(block_count: int, requested: int, seed: int = 42, **kwargs):
    return ResidualVectorBootstrapScenarioGenerator().generate(
        _forecast(**kwargs), _residuals(block_count, **kwargs),
        n_scenarios=requested, seed=seed,
    )


def _origins(bundle):
    return [
        scenario.metadata["sampled_blocks"][STORE]["forecast_origin"]
        for scenario in bundle.scenarios
    ]


def _inventory_scenarios(bundle):
    return [
        InventoryDemandScenario(
            scenario_id=scenario.scenario_id,
            probability_weight=scenario.probability_weight,
            simulation_start_date=FORECAST_DATE + timedelta(days=1),
            simulation_end_date=FORECAST_DATE + timedelta(days=1),
            lines=[InventoryDemandLine(
                scenario_id=scenario.scenario_id,
                store_id=STORE,
                ingredient_id="ingredient",
                target_date=FORECAST_DATE + timedelta(days=1),
                quantity=scenario.lines[0].demand_quantity,
                unit="each",
            )],
        )
        for scenario in bundle.scenarios
    ]


def _design():
    return [InventoryDemandScenario(
        scenario_id="design",
        probability_weight=None,
        simulation_start_date=FORECAST_DATE + timedelta(days=1),
        simulation_end_date=FORECAST_DATE + timedelta(days=1),
        lines=[InventoryDemandLine(
            scenario_id="design", store_id=STORE, ingredient_id="ingredient",
            target_date=FORECAST_DATE + timedelta(days=1), quantity=10.0, unit="each",
        )],
    )]


def test_small_pool_below_support_samples_distinct_blocks_reproducibly():
    bundle = _generate(11, 10, seed=42)
    repeated = _generate(11, 10, seed=42)

    assert MAX_UNIQUE_COHERENT_BLOCK_POOL == 100
    assert len(bundle.scenarios) == 10
    assert len(set(_origins(bundle))) == 10
    assert _origins(bundle) == _origins(repeated)
    assert [row.probability_weight for row in bundle.scenarios] == [pytest.approx(0.1)] * 10
    assert bundle.diagnostics["eligible_coherent_block_count"] == 11
    assert bundle.diagnostics["scenario_count_requested"] == 10
    assert bundle.diagnostics["scenario_count_generated"] == 10
    assert bundle.diagnostics["selected_coherent_block_count"] == 10
    assert bundle.diagnostics["distinct_selected_coherent_block_count"] == 10
    assert bundle.diagnostics["coherent_block_sampling_mode"] == "without_replacement"
    selection = select_stochastic_scenarios(_inventory_scenarios(bundle), _design())
    assert selection.effective_scenario_count == pytest.approx(10.0)
    assert selection.stochastic_saa_enabled is True


@pytest.mark.parametrize("requested", [50, 100])
def test_small_pool_enumerates_all_support_once_when_request_is_larger(requested):
    bundle = _generate(11, requested, seed=42)
    different_seed = _generate(11, requested, seed=7)

    assert len(bundle.scenarios) == 11
    assert len(set(_origins(bundle))) == 11
    assert _origins(bundle) == _origins(different_seed)
    assert [row.probability_weight for row in bundle.scenarios] == [pytest.approx(1 / 11)] * 11
    assert bundle.diagnostics["selected_coherent_block_count"] == 11
    assert bundle.diagnostics["distinct_selected_coherent_block_count"] == 11
    assert bundle.diagnostics["coherent_block_sampling_mode"] == "enumerate_all"
    assert bundle.diagnostics["scenario_count_requested"] == requested
    assert bundle.diagnostics["scenario_count_generated"] == 11
    selection = select_stochastic_scenarios(_inventory_scenarios(bundle), _design())
    assert selection.scenario_count_generated == 11
    assert selection.effective_scenario_count == pytest.approx(11.0)
    assert selection.stochastic_saa_enabled is True


def test_small_true_support_stays_insufficient_instead_of_resampling():
    bundle = _generate(6, 100)
    selection = select_stochastic_scenarios(_inventory_scenarios(bundle), _design())

    assert len(bundle.scenarios) == 6
    assert bundle.diagnostics["coherent_block_sampling_mode"] == "enumerate_all"
    assert selection.effective_scenario_count == pytest.approx(6.0)
    assert selection.stochastic_saa_enabled is False
    assert selection.fallback_reason == "insufficient_effective_scenarios"


def test_exact_effective_scenario_threshold_is_enabled_with_ten_blocks():
    bundle = _generate(10, 100)
    selection = select_stochastic_scenarios(_inventory_scenarios(bundle), _design())

    assert len(bundle.scenarios) == 10
    assert [row.probability_weight for row in bundle.scenarios] == [pytest.approx(0.1)] * 10
    assert selection.effective_scenario_count == pytest.approx(10.0)
    assert selection.stochastic_saa_enabled is True


def test_distinct_blocks_can_still_collapse_downstream_and_merge_weights():
    forecast = _forecast(products=2, horizon=1)
    residuals = _residuals(
        2, products=2, horizon=1, scaled_values=[-10.0, -20.0]
    )
    bundle = ResidualVectorBootstrapScenarioGenerator().generate(
        forecast, residuals, n_scenarios=10, seed=42
    )
    selection = select_stochastic_scenarios(_inventory_scenarios(bundle), _design())

    assert bundle.diagnostics["selected_coherent_block_count"] == 2
    assert bundle.diagnostics["distinct_selected_coherent_block_count"] == 2
    assert selection.scenario_count_generated == 2
    assert selection.unique_scenario_count == 1
    assert selection.risk_scenarios == []
    assert selection.effective_scenario_count == pytest.approx(1.0)


def test_large_pool_keeps_existing_with_replacement_bootstrap_mode():
    bundle = _generate(101, 10, seed=42)

    assert len(bundle.scenarios) == 10
    assert bundle.diagnostics["eligible_coherent_block_count"] == 101
    assert bundle.diagnostics["selected_coherent_block_count"] == 10
    assert bundle.diagnostics["coherent_block_sampling_mode"] == "bootstrap_with_replacement"
