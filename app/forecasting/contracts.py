from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ForecastTrainingResult:
    model_version: str
    artifact_directory: Path
    baseline_metrics: dict[str, Any]
    walk_forward_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    calibration_metrics: dict[str, Any]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ForecastPrediction:
    store_id: str
    product_id: str
    product_name: str
    unit: str | None
    target_date: date
    horizon: int
    p25: float
    p50: float
    p75: float
    interval_lower: float
    interval_upper: float
    baseline_p50: float
    calibration_source: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ForecastPredictionResult:
    forecast_date: date
    forecast_horizon: int
    model_version: str
    predictions: tuple[ForecastPrediction, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PredictionDifference:
    product_id: str
    target_date: date
    horizon: int
    abs_diff_p25: float
    abs_diff_p50: float
    abs_diff_p75: float
    relative_diff_p25: float | None
    relative_diff_p50: float | None
    relative_diff_p75: float | None


@dataclass(frozen=True)
class QuantileViolation:
    provider: str
    product_id: str
    target_date: date
    horizon: int
    p25: float
    p50: float
    p75: float


@dataclass(frozen=True)
class NonFinitePrediction:
    provider: str
    product_id: str
    target_date: date
    horizon: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ForecastCompatibilityReport:
    compatible: bool
    missing_keys: tuple[tuple[str, date, int], ...]
    extra_keys: tuple[tuple[str, date, int], ...]
    production_quantile_violations: tuple[QuantileViolation, ...]
    shadow_quantile_violations: tuple[QuantileViolation, ...]
    production_duplicate_keys: tuple[tuple[str, date, int], ...]
    shadow_duplicate_keys: tuple[tuple[str, date, int], ...]
    production_non_finite_predictions: tuple[NonFinitePrediction, ...]
    shadow_non_finite_predictions: tuple[NonFinitePrediction, ...]
    differences: tuple[PredictionDifference, ...]
    mean_abs_diff_p25: float | None
    mean_abs_diff_p50: float | None
    mean_abs_diff_p75: float | None
