from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from shelfcash_forecast.baselines.ets import ets_predict_rows
from shelfcash_forecast.baselines.seasonal_naive import seasonal_naive_predict
from shelfcash_forecast.calibration.cqr import (
    apply_cqr_calibrator,
    fit_cqr_calibrator,
)
from shelfcash_forecast.calibration.crossing import correct_quantile_crossing
from shelfcash_forecast.calibration.metrics import (
    calibration_breakdown_by_horizon,
    calibration_metrics,
)
from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.contracts import TrainingResult
from shelfcash_forecast.data.adapter import adapt_forecast_input
from shelfcash_forecast.data.demand_reconstruction import reconstruct_demand
from shelfcash_forecast.data.panel_builder import build_daily_panel, resolve_missing_sales
from shelfcash_forecast.data.validator import validate_calendar, validate_sales
from shelfcash_forecast.evaluation.metrics import (
    evaluate_point_prediction,
    evaluate_quantile_predictions,
    metric_breakdown_by_horizon,
)
from shelfcash_forecast.evaluation.splits import apply_final_split, make_final_split
from shelfcash_forecast.evaluation.walk_forward import run_walk_forward
from shelfcash_forecast.features.future import (
    add_calendar_future_features,
    add_deterministic_future_features,
)
from shelfcash_forecast.features.historical import add_historical_features
from shelfcash_forecast.features.specification import CategoryEncoder, normalize_model_numeric_features
from shelfcash_forecast.features.training_table import (
    add_target_seasonal_lags,
    build_training_table,
)
from shelfcash_forecast.models.predictor import predict_raw_quantiles
from shelfcash_forecast.models.trainer import train_model_bundle
from shelfcash_forecast.registry.writer import write_artifacts


def _prepare_modelling_table(
    canonical_data: Mapping[str, pd.DataFrame],
    config: ForecastConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None, dict[str, object]]:
    adapted = adapt_forecast_input(canonical_data, config)
    sales, quality_report = validate_sales(adapted.sales_history)
    calendar = validate_calendar(adapted.calendar_features, quality_report)

    panel = build_daily_panel(sales, calendar, adapted.inventory_availability)
    panel = resolve_missing_sales(panel)
    panel = reconstruct_demand(panel)
    panel = add_historical_features(panel, config)

    table = build_training_table(panel, config)
    table = add_target_seasonal_lags(table, panel, config)
    table = add_deterministic_future_features(table)
    table = add_calendar_future_features(table, calendar)
    quality = quality_report.to_dict()
    reconstructed = panel["stockout_reconstruction_source"].isin(
        ["is_stockout_historical_median", "inventory_historical_median"]
    )
    quality["stockout_reconstruction_used"] = bool(reconstructed.any())
    quality["stockout_reconstruction_confidence"] = (
        float(panel.loc[reconstructed, "stockout_reconstruction_confidence"].mean())
        if reconstructed.any() else 0.0
    )
    if not panel["is_stockout"].notna().any() and not reconstructed.any():
        quality["warnings"].append("STOCKOUT_RECONSTRUCTION_UNAVAILABLE")
    return table, panel, calendar, quality


def _eligible_rows(frame: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    return frame.loc[
        frame["target_train_eligible"].astype("boolean").fillna(False)
        & frame["target"].notna()
        & frame["history_observation_count"].ge(
            config.minimum_history_observations
        )
    ].copy()


def train_forecast_core(
    canonical_data: Mapping[str, pd.DataFrame],
    artifact_directory: str | Path,
    config: ForecastConfig | None = None,
    model_version: str = "forecast-core-v0.1.0",
) -> TrainingResult:
    config = config or ForecastConfig()
    table, panel, _calendar, quality_report = _prepare_modelling_table(
        canonical_data, config
    )

    split_source = table.loc[table["target"].notna()].copy()
    split = make_final_split(
        split_source,
        calibration_days=config.calibration_days,
        test_days=config.test_days,
        minimum_train_dates=config.walk_forward_minimum_train_days,
    )
    train_period, calibration_period, test_period = apply_final_split(table, split)

    walk_forward_predictions, walk_forward_metrics = run_walk_forward(
        train_period, config
    )

    train = _eligible_rows(train_period, config)
    calibration = _eligible_rows(calibration_period, config)
    test = _eligible_rows(test_period, config)
    if train.empty or calibration.empty or test.empty:
        raise ValueError(
            "Train/calibration/test rỗng sau khi lọc. Hãy kiểm tra số ngày lịch sử, "
            "stockout và missing sales."
        )

    encoder = CategoryEncoder.fit(train)
    encoded_train = normalize_model_numeric_features(encoder.transform(train))
    encoded_calibration = normalize_model_numeric_features(encoder.transform(calibration))
    encoded_test = normalize_model_numeric_features(encoder.transform(test))

    model_bundle = train_model_bundle(encoded_train, config)

    calibration_predictions = correct_quantile_crossing(
        predict_raw_quantiles(model_bundle, encoded_calibration)
    )
    calibrator = fit_cqr_calibrator(
        calibration_predictions,
        desired_coverage=config.nominal_coverage,
        minimum_samples=config.minimum_calibration_samples,
    )

    test_predictions = correct_quantile_crossing(
        predict_raw_quantiles(model_bundle, encoded_test)
    )
    test_predictions = apply_cqr_calibrator(test_predictions, calibrator)
    test_predictions["baseline_p50"] = seasonal_naive_predict(test).to_numpy()
    test_predictions["ets_p50"] = ets_predict_rows(
        test,
        panel,
        fallback=test_predictions["baseline_p50"],
    ).to_numpy()

    test_metrics = {
        "overall": evaluate_quantile_predictions(test_predictions),
        "by_horizon": metric_breakdown_by_horizon(test_predictions),
    }
    baseline_metrics = {
        "seasonal_naive": evaluate_point_prediction(
            test_predictions, "baseline_p50"
        ),
        "ets": evaluate_point_prediction(test_predictions, "ets_p50"),
    }
    calibration_metrics_payload = {
        "overall": calibration_metrics(
            test_predictions, config.nominal_coverage
        ),
        "by_horizon": calibration_breakdown_by_horizon(
            test_predictions, config.nominal_coverage
        ),
    }

    warnings = list(quality_report.get("warnings", []))
    if not table["target_planned_price"].notna().any():
        warnings.append("FUTURE_PLANNED_PRICE_MISSING")
    if not table["target_promotion_known"].eq(1).any():
        warnings.append("FUTURE_PROMOTION_PLAN_MISSING")
    model_wape = test_metrics["overall"].get("wape_p50")
    baseline_wape = baseline_metrics["seasonal_naive"].get("wape")
    if (
        model_wape is not None
        and baseline_wape is not None
        and model_wape > baseline_wape
    ):
        warnings.append("MODEL_WORSE_THAN_SEASONAL_NAIVE")

    metadata = {
        "model_version": model_version,
        "target": "product_demand_proxy",
        "source_target_column": "quantity_sold",
        "grain": "store_product_day",
        "horizons": list(config.horizons),
        "quantiles": list(config.quantiles),
        "nominal_interval_coverage": config.nominal_coverage,
        "censored_target_policy": "exclude_when_stockout_is_known",
        "missing_stockout_policy": "use_observed_sales_with_warning",
        "future_price_used": bool(table["target_planned_price"].notna().any()),
        "future_price_source": "calendar_features.planned_price" if table["target_planned_price"].notna().any() else None,
        "future_promotion_used": bool(table["target_promotion_known"].eq(1).any()),
        "future_promotion_source": "calendar_features" if table["target_promotion_known"].eq(1).any() else None,
        "stockout_reconstruction_used": quality_report["stockout_reconstruction_used"],
        "stockout_reconstruction_confidence": quality_report["stockout_reconstruction_confidence"],
        "feature_coverage": {
            "planned_price": float(table["target_planned_price"].notna().mean()),
            "historical_price": float(table["last_observed_price"].notna().mean()),
            "future_promotion": float(table["target_promotion_known"].eq(1).mean()),
            "discount_rate": float(table["target_discount_rate"].notna().mean()),
            "calendar": float(table["calendar_available"].eq(1).mean()),
            "stockout_direct": float(panel["is_stockout"].notna().mean()),
            "stockout_reconstructed": float(panel["stockout_reconstruction_source"].isin(["is_stockout_historical_median", "inventory_historical_median"]).mean()),
        },
        "missing_feature_warnings": sorted(w for w in warnings if "MISSING" in w or "UNAVAILABLE" in w),
        "split": split.to_dict(),
        "warnings": sorted(set(warnings)),
    }
    training_manifest = {
        "sales_rows": int(quality_report["rows_valid"]),
        "panel_rows": int(len(panel)),
        "training_table_rows": int(len(table)),
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "test_rows": int(len(test)),
        "store_count": int(panel["store_key"].nunique()),
        "product_count": int(panel["product_key"].nunique()),
        "history_start": panel["date"].min().date().isoformat(),
        "history_end": panel["date"].max().date().isoformat(),
    }

    artifact_dir = write_artifacts(
        artifact_directory=artifact_directory,
        model_bundle=model_bundle,
        calibrator=calibrator,
        encoder=encoder,
        config=config,
        model_version=model_version,
        metadata=metadata,
        quality_report=quality_report,
        baseline_metrics=baseline_metrics,
        walk_forward_metrics=walk_forward_metrics,
        test_metrics=test_metrics,
        calibration_metrics_payload=calibration_metrics_payload,
        training_manifest=training_manifest,
        predictions={
            "walk_forward_predictions": walk_forward_predictions,
            "calibration_predictions": calibration_predictions,
            "test_predictions": test_predictions,
        },
    )

    return TrainingResult(
        status="success",
        model_version=model_version,
        artifact_directory=str(artifact_dir),
        data_quality=quality_report,
        baseline_metrics=baseline_metrics,
        walk_forward_metrics=walk_forward_metrics,
        test_metrics=test_metrics,
        calibration_metrics=calibration_metrics_payload,
        warnings=sorted(set(warnings)),
    )
