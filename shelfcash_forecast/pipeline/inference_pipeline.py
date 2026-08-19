from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from shelfcash_forecast.baselines.seasonal_naive import seasonal_naive_predict
from shelfcash_forecast.calibration.cqr import apply_cqr_calibrator
from shelfcash_forecast.calibration.crossing import correct_quantile_crossing
from shelfcash_forecast.contracts import ForecastPackage, ForecastPrediction
from shelfcash_forecast.data.adapter import adapt_forecast_input
from shelfcash_forecast.data.demand_reconstruction import reconstruct_demand
from shelfcash_forecast.data.panel_builder import build_daily_panel, resolve_missing_sales
from shelfcash_forecast.data.validator import validate_calendar, validate_sales
from shelfcash_forecast.exceptions import InsufficientDataError
from shelfcash_forecast.features.future import (
    add_calendar_future_features,
    add_deterministic_future_features,
)
from shelfcash_forecast.features.historical import add_historical_features
from shelfcash_forecast.features.specification import validate_runtime_feature_schema
from shelfcash_forecast.features.training_table import (
    add_target_seasonal_lags,
    build_runtime_rows,
)
from shelfcash_forecast.models.predictor import predict_raw_quantiles
from shelfcash_forecast.registry.loader import load_artifacts


def predict_demand(
    canonical_data: Mapping[str, pd.DataFrame],
    artifact_directory: str | Path,
    cutoff_date: str | pd.Timestamp,
    forecast_horizon: int = 7,
) -> ForecastPackage:
    """Load immutable artifacts and forecast strictly after the inclusive cutoff."""

    artifacts = load_artifacts(artifact_directory)
    config = artifacts.config
    cutoff = pd.Timestamp(cutoff_date).normalize()
    if forecast_horizon < 1 or forecast_horizon > max(config.horizons):
        raise ValueError(f"forecast_horizon must be in 1..{max(config.horizons)}.")

    adapted = adapt_forecast_input(canonical_data, config)
    sales, quality_report = validate_sales(adapted.sales_history)
    calendar = validate_calendar(adapted.calendar_features, quality_report)
    sales = sales.loc[sales["date"].le(cutoff)].copy()
    if sales.empty:
        raise InsufficientDataError("No sales_history exists at or before cutoff_date.")

    panel = build_daily_panel(sales, calendar, end_date=cutoff)
    panel = resolve_missing_sales(panel)
    panel = reconstruct_demand(panel, config)
    panel = add_historical_features(panel, config)
    runtime = build_runtime_rows(panel, cutoff, forecast_horizon)
    runtime = add_target_seasonal_lags(runtime, panel, config)
    runtime = add_deterministic_future_features(runtime)
    runtime = add_calendar_future_features(runtime, calendar)
    runtime = artifacts.encoder.transform(runtime)
    validate_runtime_feature_schema(
        runtime,
        expected_features=artifacts.model_bundle.feature_names,
        expected_categorical_features=artifacts.model_bundle.categorical_features,
    )

    runtime = correct_quantile_crossing(
        predict_raw_quantiles(artifacts.model_bundle, runtime)
    )
    runtime = apply_cqr_calibrator(runtime, artifacts.calibrator)
    runtime["baseline_p50"] = seasonal_naive_predict(runtime).to_numpy()
    closed = runtime["target_store_closed"].eq(1)
    for column in (
        "p25",
        "p50",
        "p75",
        "interval_lower",
        "interval_upper",
        "baseline_p50",
    ):
        runtime.loc[closed, column] = 0.0

    global_warnings = list(quality_report.warnings) + list(artifacts.warnings)
    predictions: list[ForecastPrediction] = []
    for row in runtime.itertuples(index=False):
        warnings: list[str] = []
        if row.product_code == -1:
            warnings.append("UNSEEN_PRODUCT")
        if row.calibration_source == "global":
            warnings.append("CALIBRATION_FALLBACK_GLOBAL")
        if pd.isna(row.seasonal_lag_7_target):
            warnings.append("INSUFFICIENT_SEASONAL_HISTORY")
        if row.target_store_closed == 1:
            warnings.append("STORE_PLANNED_CLOSED")
        if row.history_observation_count < config.minimum_history_observations:
            warnings.append("INSUFFICIENT_HISTORY")
        predictions.append(
            ForecastPrediction(
                store_id=str(row.store_key),
                product_id=str(row.product_key),
                product_name=str(row.product_name),
                unit=None if pd.isna(row.unit) else str(row.unit),
                target_date=pd.Timestamp(row.target_date).date(),
                horizon=int(row.horizon),
                p25=float(row.p25),
                p50=float(row.p50),
                p75=float(row.p75),
                interval_lower=float(row.interval_lower),
                interval_upper=float(row.interval_upper),
                baseline_p50=float(row.baseline_p50),
                calibration_source=str(row.calibration_source),
                warnings=warnings,
            )
        )
    return ForecastPackage(
        forecast_date=cutoff.date(),
        forecast_horizon=forecast_horizon,
        model_version=str(artifacts.metadata["model_version"]),
        predictions=predictions,
        warnings=sorted(set(global_warnings)),
    )
