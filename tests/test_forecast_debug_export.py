from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from shelfcash_core.debug_export import ForecastDebugExport
from shelfcash_core.models.predictor import predict_raw_quantiles
from shelfcash_core.models.quantile_models import QuantileModelBundle, train_quantile_models


class _CapturingModel:
    def __init__(self) -> None:
        self.inputs: list[pd.DataFrame] = []

    def predict(self, data: pd.DataFrame) -> np.ndarray:
        self.inputs.append(data.copy(deep=True))
        return np.arange(len(data), dtype=float)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_date": pd.to_datetime(["2026-08-04", "2026-08-05"]),
            "store_key": ["STORE_001", "STORE_001"],
            "product_key": ["tea", "tea"],
            "product_name": ["Trà", "Trà"],
            "unit": ["cup", "cup"],
            "product_code": pd.Series([2, 2], dtype="int32"),
            "horizon": pd.Series([1.0, 2.0], dtype="float64"),
            "lag_1": [10.5, np.nan],
        }
    )


def _bundle(model: _CapturingModel) -> QuantileModelBundle:
    return QuantileModelBundle(
        models={0.25: model, 0.50: model, 0.75: model},
        feature_names=["product_code", "horizon", "lag_1"],
        categorical_features=["product_code"],
    )


def test_inference_export_is_exact_model_input_and_side_effect_free(tmp_path):
    frame = _frame()
    before = frame.copy(deep=True)
    model = _CapturingModel()
    bundle = _bundle(model)

    result = predict_raw_quantiles(
        bundle,
        frame,
        debug_export=ForecastDebugExport(enabled=True, output_directory=tmp_path, run_id="run-1"),
    )

    assert_frame_equal(frame, before, check_exact=True)
    assert_frame_equal(model.inputs[0], before[bundle.feature_names], check_exact=True)
    exported = pd.read_csv(tmp_path / "run-1" / "forecast_features.csv", encoding="utf-8-sig")
    assert len(exported) == len(model.inputs[0])
    assert exported.columns.tolist() == [
        "target_date", "store_key", "product_key", "product_name", "unit",
        *bundle.feature_names,
    ]
    assert exported["product_key"].tolist() == ["tea", "tea"]
    assert result[["p25_raw", "p50_raw", "p75_raw"]].notna().all().all()


def test_disabled_export_creates_no_file_and_keeps_predictions(tmp_path):
    model = _CapturingModel()
    result = predict_raw_quantiles(
        _bundle(model),
        _frame(),
        debug_export=ForecastDebugExport(enabled=False, output_directory=tmp_path, run_id="run-disabled"),
    )

    assert not (tmp_path / "run-disabled" / "forecast_features.csv").exists()
    assert result["p50_raw"].tolist() == [0.0, 1.0]


def test_export_failure_does_not_interrupt_prediction(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise OSError("disk unavailable")

    warnings: list[str] = []
    monkeypatch.setattr("shelfcash_core.debug_export.export_forecast_features_csv", _fail)
    monkeypatch.setattr(
        "shelfcash_core.debug_export.logger.warning",
        lambda message, *args, **kwargs: warnings.append(message % args),
    )

    result = predict_raw_quantiles(
        _bundle(_CapturingModel()),
        _frame(),
        debug_export=ForecastDebugExport(enabled=True, output_directory=tmp_path, run_id="run-error"),
    )

    assert result["p50_raw"].tolist() == [0.0, 1.0]
    assert warnings == ["Forecast debug export failed: disk unavailable"]


def test_training_export_includes_the_actual_fit_target_and_feature_order(tmp_path, monkeypatch):
    fitted: list[tuple[pd.DataFrame, pd.Series]] = []

    class _FitModel:
        def fit(self, x, y, **kwargs):
            fitted.append((x.copy(deep=True), y.copy(deep=True)))

    monkeypatch.setattr(
        "shelfcash_core.models.quantile_models.build_quantile_model",
        lambda *args, **kwargs: _FitModel(),
    )
    train = _frame().assign(target=pd.Series([12.25, 8.5], dtype="float64"))
    features = ["lag_1", "product_code", "horizon"]

    train_quantile_models(
        train,
        feature_names=features,
        categorical_features=["product_code"],
        quantiles=(0.25, 0.50, 0.75),
        base_params={},
        debug_export=ForecastDebugExport(
            enabled=True,
            output_directory=tmp_path,
            run_id="training-run",
            filename="forecast_training_features.csv",
        ),
    )

    exported = pd.read_csv(
        tmp_path / "training-run" / "forecast_training_features.csv",
        encoding="utf-8-sig",
    )
    assert exported.columns.tolist() == [
        "target_date", "store_key", "product_key", "product_name", "unit", "target", *features,
    ]
    assert exported["target"].tolist() == [12.25, 8.5]
    assert_frame_equal(fitted[0][0], train[features], check_exact=True)
    assert fitted[0][1].tolist() == train["target"].tolist()
