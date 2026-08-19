"""Shadow-only utilities for forecast-core parity qualification.

These helpers operate on backend-owned canonical data and never use application
persistence.  Callers choose the output path for any local diagnostic report.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from app.forecasting.comparator import compare_forecasts
from app.forecasting.contracts import ForecastPredictionResult


def feature_pipeline_report(existing_table: pd.DataFrame, shadow_table: pd.DataFrame) -> dict[str, Any]:
    """Compare aggregate feature schema/data parity without returning sales rows."""
    existing_columns = list(existing_table.columns)
    shadow_columns = list(shadow_table.columns)
    common = [column for column in existing_columns if column in shadow_table.columns]
    value_mismatch_columns = 0
    for column in common:
        left, right = existing_table[column], shadow_table[column]
        if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
            equal = np.isclose(left.to_numpy(dtype=float), right.to_numpy(dtype=float), equal_nan=True).all()
        else:
            equal = left.fillna("<NA>").astype(str).equals(right.fillna("<NA>").astype(str))
        value_mismatch_columns += int(not equal)
    return {
        "training_rows": {"existing": len(existing_table), "shadow": len(shadow_table)},
        "features": {
            "missing_in_shadow": sorted(set(existing_columns) - set(shadow_columns)),
            "extra_in_shadow": sorted(set(shadow_columns) - set(existing_columns)),
            "order_equal": existing_columns == shadow_columns,
            "dtype_mismatches": sorted(column for column in common if str(existing_table[column].dtype) != str(shadow_table[column].dtype)),
            "value_mismatch_columns": value_mismatch_columns,
        },
    }


def prediction_parity_report(production: ForecastPredictionResult, shadow: ForecastPredictionResult) -> dict[str, Any]:
    comparison = compare_forecasts(production, shadow)
    fields = ("p25", "p50", "p75", "interval_lower", "interval_upper", "baseline_p50")
    production_by_key = {(item.product_id, item.target_date, item.horizon): item for item in production.predictions}
    shadow_by_key = {(item.product_id, item.target_date, item.horizon): item for item in shadow.predictions}
    metrics: dict[str, dict[str, float | None]] = {}
    for field in fields:
        values = [abs(getattr(shadow_by_key[key], field) - getattr(production_by_key[key], field))
                  for key in sorted(set(production_by_key) & set(shadow_by_key))]
        metrics[field] = {
            "mae_drift": float(np.mean(values)) if values else None,
            "median_absolute_drift": float(median(values)) if values else None,
            "p95_absolute_drift": float(np.percentile(values, 95)) if values else None,
            "max_absolute_drift": float(max(values)) if values else None,
        }
    return {
        "structural": {
            "compatible": comparison.compatible,
            "missing_keys": comparison.missing_keys,
            "extra_keys": comparison.extra_keys,
            "production_duplicate_keys": comparison.production_duplicate_keys,
            "shadow_duplicate_keys": comparison.shadow_duplicate_keys,
            "production_quantile_violations": comparison.production_quantile_violations,
            "shadow_quantile_violations": comparison.shadow_quantile_violations,
            "production_non_finite_predictions": comparison.production_non_finite_predictions,
            "shadow_non_finite_predictions": comparison.shadow_non_finite_predictions,
        },
        "prediction_rows": {"existing": len(production.predictions), "shadow": len(shadow.predictions)},
        "metrics": metrics,
    }


def write_shadow_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, default=_json_default, indent=2, sort_keys=True), encoding="utf-8")


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")
