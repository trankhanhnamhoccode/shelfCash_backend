from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from shelfcash_forecast.calibration.cqr import CQRCalibrator
from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.features.specification import (
    CATEGORICAL_MODEL_COLUMNS,
    MODEL_FEATURES,
    CategoryEncoder,
)
from shelfcash_forecast.models.quantile_models import QuantileModelBundle


def _json_default(value: object) -> object:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Không thể JSON serialize {type(value)!r}")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def _save_model(model: object, path: Path) -> None:
    if isinstance(model, lgb.Booster):
        model.save_model(str(path))
        return
    booster = getattr(model, "booster_", None)
    if booster is None:
        raise TypeError(f"Model {type(model)!r} không có booster_ để lưu.")
    booster.save_model(str(path))


def write_artifacts(
    artifact_directory: str | Path,
    model_bundle: QuantileModelBundle,
    calibrator: CQRCalibrator,
    encoder: CategoryEncoder,
    config: ForecastConfig,
    model_version: str,
    metadata: dict[str, Any],
    quality_report: dict[str, Any],
    baseline_metrics: dict[str, Any],
    walk_forward_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    calibration_metrics_payload: dict[str, Any],
    training_manifest: dict[str, Any],
    predictions: dict[str, pd.DataFrame] | None = None,
) -> Path:
    artifact_dir = Path(artifact_directory)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    _save_model(model_bundle.models[0.25], artifact_dir / "model_q25.txt")
    _save_model(model_bundle.models[0.50], artifact_dir / "model_q50.txt")
    _save_model(model_bundle.models[0.75], artifact_dir / "model_q75.txt")

    write_json(artifact_dir / "calibrator.json", calibrator.to_dict())
    write_json(artifact_dir / "category_mappings.json", encoder.to_dict())
    write_json(
        artifact_dir / "feature_schema.json",
        {
            "features": list(MODEL_FEATURES),
            "categorical_features": list(CATEGORICAL_MODEL_COLUMNS),
            "schema_version": "1.0",
        },
    )
    write_json(artifact_dir / "preprocessing_config.json", config.to_dict())
    write_json(artifact_dir / "model_metadata.json", metadata)
    write_json(artifact_dir / "quality_report.json", quality_report)
    write_json(artifact_dir / "baseline_metrics.json", baseline_metrics)
    write_json(artifact_dir / "walk_forward_metrics.json", walk_forward_metrics)
    write_json(artifact_dir / "test_metrics.json", test_metrics)
    write_json(
        artifact_dir / "calibration_metrics.json",
        calibration_metrics_payload,
    )
    write_json(artifact_dir / "training_manifest.json", training_manifest)

    if predictions:
        for name, frame in predictions.items():
            if frame.empty:
                continue
            try:
                frame.to_parquet(artifact_dir / f"{name}.parquet", index=False)
            except ImportError:
                frame.to_csv(artifact_dir / f"{name}.csv", index=False)

    return artifact_dir
