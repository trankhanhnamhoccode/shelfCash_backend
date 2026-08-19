from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy import func, select

from app.config import Settings
from app.forecasting import ShelfCashForecastProvider, compare_forecasts
from app.forecasting.contracts import ForecastPrediction, ForecastPredictionResult
from app.models.business import ProductModel, SalesDailyModel
from app.models.operations import ForecastModelVersionModel, ForecastPredictionModel, ForecastResidualModel, ForecastRunModel
from app.models.store import StoreModel
from app.schemas.forecast import ForecastPredictRequest
from app.services.forecast_service import ForecastService
from shelfcash_core import ForecastConfig


def _result(value: float, *, version: str = "model") -> ForecastPredictionResult:
    return ForecastPredictionResult(
        forecast_date=date(2026, 8, 1), forecast_horizon=1, model_version=version,
        warnings=(), predictions=(ForecastPrediction(
            store_id="STORE_001", product_id="product-1", product_name="Tea", unit="cup",
            target_date=date(2026, 8, 2), horizon=1, p25=value, p50=value + 1,
            p75=value + 2, interval_lower=value - 1, interval_upper=value + 3,
            baseline_p50=value + 1, calibration_source="test", warnings=(),
        ),),
    )


class _ProductionProvider:
    name = "existing"

    def __init__(self): self.calls = 0

    def predict(self, *args, **kwargs):
        self.calls += 1
        return _result(10, version="production-v1")


class _ShadowProvider:
    name = "shelfcash_forecast"

    def __init__(self, *, fail=False): self.calls = 0; self.fail = fail

    def predict(self, *args, **kwargs):
        self.calls += 1
        if self.fail: raise RuntimeError("shadow failure")
        return _result(12, version="shadow-v1")


def _service(session_factory, tmp_path: Path, shadow_provider, *, enabled: bool):
    settings = Settings(
        database_url="sqlite://", forecast_artifact_root=tmp_path / "artifacts",
        forecast_shadow_artifact_root=tmp_path / "shadow", forecast_shadow_enabled=enabled,
        forecast_shadow_provider="shelfcash_forecast", forecast_max_horizon=1,
    )
    production = _ProductionProvider()
    return ForecastService(session_factory, settings, production_provider=production, shadow_provider=shadow_provider), production, settings


def _seed_predictable_state(session_factory, settings, tmp_path):
    artifact = settings.forecast_artifact_root / "STORE_001" / "production-v1"
    artifact.mkdir(parents=True)
    with session_factory() as session:
        session.add(StoreModel(store_id="STORE_001", store_name="Store", timezone="Asia/Ho_Chi_Minh", currency="VND"))
        product = ProductModel(product_id="product-1", store_id="STORE_001", product="Tea", normalized_name="tea", active=True, source="test")
        session.add(product)
        session.add(SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=date(2026, 8, 1),
            product_id=product.product_id, quantity=Decimal("1"), promotion=False, is_stockout=False, source="test"))
        session.add(ForecastModelVersionModel(model_version_id=str(uuid4()), store_id="STORE_001", model_version="production-v1",
            status="ready", is_active=True, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
        session.commit()
    return artifact


def test_shadow_disabled_only_calls_production(session_factory, tmp_path):
    shadow = _ShadowProvider(); service, production, settings = _service(session_factory, tmp_path, shadow, enabled=False)
    _seed_predictable_state(session_factory, settings, tmp_path)
    result = service.predict(ForecastPredictRequest(store_id="STORE_001", cutoff_date=date(2026, 8, 1), forecast_horizon=1))
    assert result["predictions"][0]["p50"] == 11
    assert production.calls == 1 and shadow.calls == 0


def test_shadow_failure_isolated_and_does_not_persist_rows(session_factory, tmp_path, monkeypatch):
    shadow = _ShadowProvider(fail=True); service, production, settings = _service(session_factory, tmp_path, shadow, enabled=True)
    _seed_predictable_state(session_factory, settings, tmp_path)
    (settings.forecast_shadow_artifact_root / "STORE_001" / "production-v1--shelfcash_forecast").mkdir(parents=True)
    from app.services import forecast_service
    warnings = []
    monkeypatch.setattr(forecast_service.logger, "warning", lambda message, *args, **kwargs: warnings.append(message))
    result = service.predict(ForecastPredictRequest(store_id="STORE_001", cutoff_date=date(2026, 8, 1), forecast_horizon=1))
    assert result["status"] == "completed"
    assert production.calls == 1 and shadow.calls == 1
    assert any("forecast_shadow_inference_failed" in message for message in warnings)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ForecastRunModel)) == 1
        assert session.scalar(select(func.count()).select_from(ForecastPredictionModel)) == 1
        assert session.scalar(select(func.count()).select_from(ForecastResidualModel)) == 0


def test_comparator_reports_drift_without_marking_it_incompatible():
    report = compare_forecasts(_result(10), _result(12, version="shadow"))
    assert report.compatible is True
    assert report.mean_abs_diff_p50 == 2
    assert report.differences[0].relative_diff_p50 == 2 / 11


def test_new_provider_trains_loads_and_predicts_in_isolated_artifacts(tmp_path):
    cutoff = date(2026, 5, 1)
    sales = []
    for offset in range(112):
        day = cutoff - timedelta(days=111 - offset)
        sales.append({"date": day, "store_id": "STORE_001", "product_id": "product-1", "product_name": "Tea",
                      "quantity_sold": 8 + offset % 5, "unit": "cup", "is_stockout": False})
    calendar = [{"date": cutoff - timedelta(days=111) + timedelta(days=offset), "is_weekend": False,
                 "is_holiday": False, "is_store_closed": False, "is_promotion": False}
                for offset in range(114)]
    config = ForecastConfig(horizons=(1, 2), minimum_history_observations=28, calibration_days=7, test_days=7,
        minimum_calibration_samples=3, walk_forward_minimum_train_days=40, walk_forward_validation_days=7,
        walk_forward_step_days=7, walk_forward_maximum_folds=1,
        lightgbm_params={"learning_rate": 0.1, "n_estimators": 8, "num_leaves": 7, "min_child_samples": 5,
                         "random_state": 42, "n_jobs": 1, "verbosity": -1})
    provider = ShelfCashForecastProvider(); artifact = tmp_path / "shadow" / "STORE_001" / "shadow-v1"
    training = provider.train({"sales_history": pd.DataFrame(sales), "calendar_features": pd.DataFrame(calendar)}, artifact,
        config=config, model_version="shadow-v1")
    prediction = provider.predict({"sales_history": pd.DataFrame(sales), "calendar_features": pd.DataFrame(calendar)}, artifact, cutoff, 2)
    assert training.artifact_directory == artifact and (artifact / "artifact_checksums.json").is_file()
    assert prediction.model_version == "shadow-v1" and len(prediction.predictions) == 2
    assert all(item.p25 <= item.p50 <= item.p75 for item in prediction.predictions)
