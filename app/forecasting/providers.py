from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import pandas as pd

from app.forecasting.contracts import ForecastPrediction, ForecastPredictionResult, ForecastTrainingResult


class ForecastCoreProvider(Protocol):
    name: str

    def train(self, canonical_data: Mapping[str, pd.DataFrame], artifact_directory: Path, *, config, model_version: str, debug_export=None) -> ForecastTrainingResult: ...

    def predict(self, canonical_data: Mapping[str, pd.DataFrame], artifact_directory: Path, cutoff_date, forecast_horizon: int, *, debug_export=None) -> ForecastPredictionResult: ...


def _training_result(result) -> ForecastTrainingResult:
    return ForecastTrainingResult(
        model_version=str(result.model_version), artifact_directory=Path(result.artifact_directory),
        baseline_metrics=dict(result.baseline_metrics), walk_forward_metrics=dict(result.walk_forward_metrics),
        test_metrics=dict(result.test_metrics), calibration_metrics=dict(result.calibration_metrics),
        warnings=tuple(result.warnings),
    )


def _prediction_result(result) -> ForecastPredictionResult:
    return ForecastPredictionResult(
        forecast_date=result.forecast_date, forecast_horizon=int(result.forecast_horizon),
        model_version=str(result.model_version), warnings=tuple(result.warnings),
        predictions=tuple(ForecastPrediction(
            store_id=str(item.store_id), product_id=str(item.product_id), product_name=str(item.product_name),
            unit=item.unit, target_date=item.target_date, horizon=int(item.horizon), p25=float(item.p25),
            p50=float(item.p50), p75=float(item.p75), interval_lower=float(item.interval_lower),
            interval_upper=float(item.interval_upper), baseline_p50=float(item.baseline_p50),
            calibration_source=str(item.calibration_source), warnings=tuple(item.warnings),
        ) for item in result.predictions),
    )


class ExistingForecastCoreProvider:
    name = "existing"

    def train(self, canonical_data, artifact_directory, *, config, model_version, debug_export=None):
        # Resolve lazily so existing service-level monkeypatch tests retain their behavior.
        from app.services import forecast_service
        return _training_result(forecast_service.train_forecast_core(
            canonical_data, artifact_directory, config=config, model_version=model_version, debug_export=debug_export,
        ))

    def predict(self, canonical_data, artifact_directory, cutoff_date, forecast_horizon, *, debug_export=None):
        from app.services import forecast_service
        return _prediction_result(forecast_service.predict_demand(
            canonical_data, artifact_directory, cutoff_date, forecast_horizon, debug_export=debug_export,
        ))


class ShelfCashForecastProvider:
    name = "shelfcash_forecast"

    @staticmethod
    def _config(config):
        from shelfcash_forecast import ForecastConfig
        return ForecastConfig.from_dict(config.to_dict())

    def train(self, canonical_data, artifact_directory, *, config, model_version, debug_export=None):
        del debug_export  # New core has no debug-export argument in its public training API.
        from shelfcash_forecast import train_forecast_core
        return _training_result(train_forecast_core(
            canonical_data, artifact_directory, config=self._config(config), model_version=model_version,
        ))

    def predict(self, canonical_data, artifact_directory, cutoff_date, forecast_horizon, *, debug_export=None):
        del debug_export  # New core has no debug-export argument in its public inference API.
        from shelfcash_forecast import predict_demand
        return _prediction_result(predict_demand(canonical_data, artifact_directory, cutoff_date, forecast_horizon))
