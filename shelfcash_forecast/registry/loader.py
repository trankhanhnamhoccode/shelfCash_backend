from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb

from shelfcash_forecast.calibration.cqr import CQRCalibrator
from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.exceptions import ArtifactError
from shelfcash_forecast.features.specification import CategoryEncoder
from shelfcash_forecast.models.quantile_models import QuantileModelBundle


@dataclass(frozen=True)
class LoadedArtifacts:
    model_bundle: QuantileModelBundle
    calibrator: CQRCalibrator
    encoder: CategoryEncoder
    config: ForecastConfig
    metadata: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ArtifactError(f"Thiếu artifact: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_artifacts(artifact_directory: str | Path) -> LoadedArtifacts:
    artifact_dir = Path(artifact_directory)
    if not artifact_dir.is_dir():
        raise ArtifactError(f"Artifact directory không tồn tại: {artifact_dir}")

    feature_schema = _read_json(artifact_dir / "feature_schema.json")
    config = ForecastConfig.from_dict(
        _read_json(artifact_dir / "preprocessing_config.json")
    )
    metadata = _read_json(artifact_dir / "model_metadata.json")
    encoder = CategoryEncoder.from_dict(
        _read_json(artifact_dir / "category_mappings.json")
    )
    calibrator = CQRCalibrator.from_dict(
        _read_json(artifact_dir / "calibrator.json")
    )

    model_paths = {
        0.25: artifact_dir / "model_q25.txt",
        0.50: artifact_dir / "model_q50.txt",
        0.75: artifact_dir / "model_q75.txt",
    }
    for path in model_paths.values():
        if not path.exists():
            raise ArtifactError(f"Thiếu artifact: {path.name}")

    bundle = QuantileModelBundle(
        models={
            quantile: lgb.Booster(model_file=str(path))
            for quantile, path in model_paths.items()
        },
        feature_names=list(feature_schema["features"]),
        categorical_features=list(feature_schema["categorical_features"]),
    )
    return LoadedArtifacts(
        model_bundle=bundle,
        calibrator=calibrator,
        encoder=encoder,
        config=config,
        metadata=metadata,
    )
