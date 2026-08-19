from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from app.forecasting import (
    ExistingForecastCoreProvider,
    ShelfCashForecastProvider,
    feature_pipeline_report,
    prediction_parity_report,
    write_shadow_report,
)
from shelfcash_core import ForecastConfig
from shelfcash_core.calibration.crossing import correct_quantile_crossing as existing_crossing
from shelfcash_core.pipeline.training_pipeline import _eligible_rows as existing_eligible
from shelfcash_core.pipeline.training_pipeline import _prepare_modelling_table as existing_prepare
from shelfcash_forecast.calibration.crossing import correct_quantile_crossing as shadow_crossing
from shelfcash_forecast.config import ForecastConfig as ShadowForecastConfig
from shelfcash_forecast.pipeline.training_pipeline import _eligible_rows as shadow_eligible
from shelfcash_forecast.pipeline.training_pipeline import _prepare_modelling_table as shadow_prepare


def _config() -> ForecastConfig:
    return ForecastConfig(
        horizons=(1, 2, 3, 4, 5, 6, 7),
        minimum_history_observations=28,
        calibration_days=7,
        test_days=7,
        minimum_calibration_samples=3,
        walk_forward_minimum_train_days=40,
        walk_forward_validation_days=7,
        walk_forward_step_days=7,
        walk_forward_maximum_folds=1,
        lightgbm_params={
            "learning_rate": 0.1, "n_estimators": 8, "num_leaves": 7,
            "min_child_samples": 5, "random_state": 42, "n_jobs": 1, "verbosity": -1,
        },
    )


def _canonical_data(*, include_short_product: bool = False) -> tuple[dict[str, pd.DataFrame], date]:
    cutoff = date(2026, 5, 1)
    start = cutoff - timedelta(days=111)
    sales = []
    for offset in range(112):
        day = start + timedelta(days=offset)
        # Includes an observed zero, a stockout, and a deliberately missing sales day.
        if offset != 20:
            sales.append({"date": day, "store_id": "STORE_001", "product_id": "product-1",
                          "product_name": "Tea", "quantity_sold": 0 if offset == 10 else 8 + offset % 5,
                          "unit": "cup", "is_stockout": offset == 30})
    if include_short_product:
        sales.append({"date": cutoff, "store_id": "STORE_001", "product_id": "product-short",
                      "product_name": "New tea", "quantity_sold": 2, "unit": "cup", "is_stockout": False})
    calendar = []
    for offset in range(119):
        day = start + timedelta(days=offset)
        calendar.append({"date": day, "is_weekend": False, "is_holiday": False,
                         "is_store_closed": day == cutoff + timedelta(days=3), "is_promotion": False})
    return {"sales_history": pd.DataFrame(sales), "calendar_features": pd.DataFrame(calendar)}, cutoff


def test_feature_and_reconstruction_pipeline_parity(tmp_path):
    canonical_data, _ = _canonical_data()
    config = _config()
    existing_table, existing_panel, _, _ = existing_prepare(canonical_data, config)
    shadow_table, shadow_panel, _, _ = shadow_prepare(canonical_data, ShadowForecastConfig.from_dict(config.to_dict()))
    report = feature_pipeline_report(existing_table, shadow_table)
    assert report["training_rows"]["existing"] == report["training_rows"]["shadow"]
    assert report["features"] == {
        "missing_in_shadow": [], "extra_in_shadow": [], "order_equal": True,
        "dtype_mismatches": [], "value_mismatch_columns": 0,
    }
    # Missing day is materialized; observed zero and stockout remain distinguishable input semantics.
    assert len(existing_panel) == len(shadow_panel)
    assert existing_panel["reconstructed_demand"].equals(shadow_panel["reconstructed_demand"])
    assert (existing_panel["is_stockout"] == shadow_panel["is_stockout"]).all()
    output = tmp_path / "forecast_shadow_reports" / "feature-report.json"
    write_shadow_report(output, {"source": "SYNTHETIC_FIXTURE", "features": report})
    assert json.loads(output.read_text(encoding="utf-8"))["features"]["features"]["order_equal"] is True


def test_training_prediction_and_artifact_parity_h1_h3_h7(tmp_path):
    canonical_data, cutoff = _canonical_data()
    config = _config()
    existing = ExistingForecastCoreProvider()
    shadow = ShelfCashForecastProvider()
    existing_artifact = tmp_path / "production-equivalent" / "existing-v1"
    shadow_artifact = tmp_path / "shadow" / "shadow-v1"
    existing_training = existing.train(canonical_data, existing_artifact, config=config, model_version="existing-v1")
    shadow_training = shadow.train(canonical_data, shadow_artifact, config=config, model_version="shadow-v1")
    assert (existing_artifact / "artifact_checksums.json").is_file()
    assert (shadow_artifact / "artifact_checksums.json").is_file()
    assert existing_training.calibration_metrics == shadow_training.calibration_metrics
    for horizon in (1, 3, 7):
        production = existing.predict(canonical_data, existing_artifact, cutoff, horizon)
        candidate = shadow.predict(canonical_data, shadow_artifact, cutoff, horizon)
        report = prediction_parity_report(production, candidate)
        assert report["structural"]["compatible"] is True
        assert report["prediction_rows"] == {"existing": horizon, "shadow": horizon}
        assert all(values["max_absolute_drift"] == pytest.approx(0.0) for values in report["metrics"].values())
        # H3 target date is closed by the canonical calendar: both output and baseline are zero.
        if horizon == 3:
            assert production.predictions[-1].p50 == candidate.predictions[-1].p50 == 0.0
            assert production.predictions[-1].baseline_p50 == candidate.predictions[-1].baseline_p50 == 0.0


def test_edge_case_and_crossing_parity(tmp_path):
    config = _config()
    boundary = pd.DataFrame({"target_train_eligible": [True, True, True], "target": [1, 1, 1],
                             "history_observation_count": [27, 28, 29]})
    existing = existing_eligible(boundary, config)
    shadow = shadow_eligible(boundary, ShadowForecastConfig.from_dict(config.to_dict()))
    assert existing["history_observation_count"].tolist() == shadow["history_observation_count"].tolist() == [28, 29]
    raw = pd.DataFrame({"p25_raw": [3.0, 1.0], "p50_raw": [2.0, 3.0], "p75_raw": [1.0, 2.0]})
    assert existing_crossing(raw).equals(shadow_crossing(raw))

    canonical_data, cutoff = _canonical_data(include_short_product=True)
    existing_provider, shadow_provider = ExistingForecastCoreProvider(), ShelfCashForecastProvider()
    existing_artifact, shadow_artifact = tmp_path / "existing", tmp_path / "shadow"
    existing_provider.train(canonical_data, existing_artifact, config=config, model_version="existing-edge")
    shadow_provider.train(canonical_data, shadow_artifact, config=config, model_version="shadow-edge")
    existing_prediction = existing_provider.predict(canonical_data, existing_artifact, cutoff, 1)
    shadow_prediction = shadow_provider.predict(canonical_data, shadow_artifact, cutoff, 1)
    existing_short = next(item for item in existing_prediction.predictions if item.product_id == "product-short")
    shadow_short = next(item for item in shadow_prediction.predictions if item.product_id == "product-short")
    assert existing_short.warnings == shadow_short.warnings
    assert {"UNSEEN_PRODUCT", "INSUFFICIENT_HISTORY"}.issubset(existing_short.warnings)
