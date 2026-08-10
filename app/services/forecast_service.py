from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ForecastError, InsufficientTrainingDataError, ModelNotReadyError
from app.models.operations import ForecastModelVersionModel, ForecastPredictionModel, ForecastRunModel, ForecastResidualModel
from app.models.business import SalesDailyModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.forecast_data import ForecastDataRepository
from app.repositories.forecasts import ForecastRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.core.provenance import canonical_hash
from shelfcash_core import ForecastConfig, predict_demand, train_forecast_core
from shelfcash_core.exceptions import ArtifactError, DataValidationError, FeatureSchemaError, FeatureTypeError, InsufficientDataError

logger = logging.getLogger("shelfcash.forecast")
REQUIRED_ARTIFACTS = {"model_q25.txt", "model_q50.txt", "model_q75.txt", "calibrator.json",
                      "category_mappings.json", "feature_schema.json", "preprocessing_config.json", "model_metadata.json"}


def _json(value): return json.dumps(value, ensure_ascii=False, default=str)
def _now(): return datetime.now(timezone.utc)


class ForecastService:
    def __init__(self, session_factory, settings):
        self.session_factory, self.settings = session_factory, settings

    def _component(self, value: str, field: str) -> str:
        from app.schemas.forecast import SAFE_VERSION
        if not SAFE_VERSION.fullmatch(value):
            raise ForecastError("FORECAST_INPUT_INVALID", f"{field} không hợp lệ.", {field: value})
        return value

    def _artifact_dir(self, store_id: str, version: str) -> Path:
        store_id, version = self._component(store_id, "store_id"), self._component(version, "model_version")
        root = self.settings.forecast_artifact_root.resolve()
        path = (root / store_id / version).resolve()
        if root not in path.parents: raise ForecastError("FORECAST_INPUT_INVALID", "Artifact path không hợp lệ.")
        return path

    def _config(self): return ForecastConfig(horizons=tuple(range(1, self.settings.forecast_max_horizon + 1)))

    def _core_config(self):
        """Accept a temporarily monkey-patched legacy config during migration."""
        config = self._config()
        if isinstance(config, ForecastConfig):
            return config
        if hasattr(config, "to_dict"):
            return ForecastConfig.from_dict(config.to_dict())
        raise TypeError("Forecast configuration must be a ShelfCash Core contract.")

    def train(self, body, request_id=None):
        started = time.monotonic(); version = body.model_version or self.settings.forecast_default_model_version
        history_days = body.history_days or self.settings.forecast_history_days
        start = body.cutoff_date - timedelta(days=history_days - 1)
        final = self._artifact_dir(body.store_id, version)
        staging = final.parent / f".{version}.staging-{uuid4().hex}"
        logger.info("forecast_training_started request_id=%s store_id=%s model_version=%s cutoff_date=%s", request_id, body.store_id, version, body.cutoff_date)
        with self.session_factory() as session:
            StoreRepository(session).get_required(body.store_id)
            repo = ForecastRepository(session)
            if repo.model(body.store_id, version) is None:
                session.add(ForecastModelVersionModel(model_version_id=str(uuid4()), store_id=body.store_id,
                    model_version=version, status="training", is_active=False, created_at=_now(), updated_at=_now()))
                session.commit()
        try:
            with self.session_factory() as session:
                data = ForecastDataRepository(session)
                sales = data.sales_history(body.store_id, start, body.cutoff_date)
                calendar = data.calendar_features(body.store_id, start, body.cutoff_date + timedelta(days=self.settings.forecast_max_horizon))
        except Exception as exc:
            self._mark_training_failed(body.store_id, version, exc)
            logger.exception("forecast_training_failed request_id=%s store_id=%s model_version=%s duration=%.3f", request_id, body.store_id, version, time.monotonic()-started)
            self._raise_core(exc, training=True)
        try:
            result = train_forecast_core({"sales_history": sales, "calendar_features": calendar}, staging,
                                         config=self._core_config(), model_version=version)
            missing = REQUIRED_ARTIFACTS - {p.name for p in staging.iterdir()}
            if missing: raise ArtifactError(f"Thiếu artifacts: {sorted(missing)}")
            final.parent.mkdir(parents=True, exist_ok=True)
            backup = final.parent / f".{version}.backup-{uuid4().hex}"
            if final.exists(): final.replace(backup)
            try: staging.replace(final)
            except Exception:
                if backup.exists(): backup.replace(final)
                raise
            if backup.exists(): shutil.rmtree(backup)
            manifest = json.loads((final / "training_manifest.json").read_text(encoding="utf-8"))
            with self.session_factory() as session:
                repo = ForecastRepository(session); model = repo.model(body.store_id, version)
                repo.deactivate_all(body.store_id)
                if model is None:
                    model = ForecastModelVersionModel(model_version_id=str(uuid4()), store_id=body.store_id,
                        model_version=version, created_at=_now(), updated_at=_now(), status="ready", is_active=True)
                    session.add(model)
                model.artifact_key = f"{body.store_id}/{version}"; model.status = "ready"; model.is_active = True
                model.trained_at = _now(); model.history_start = date.fromisoformat(manifest["history_start"])
                model.history_end = date.fromisoformat(manifest["history_end"]); model.metrics_json = _json({
                    "baseline": result.baseline_metrics, "walk_forward": result.walk_forward_metrics,
                    "test": result.test_metrics, "calibration": result.calibration_metrics})
                model.warnings_json = _json(result.warnings); model.updated_at = _now()
                AuditService(AuditLogRepository(session)).record(store_id=body.store_id, action="forecast_model_trained",
                    resource_type="forecast_model", resource_id=model.model_version_id,
                    after={"model_version": version, "history_start": model.history_start, "history_end": model.history_end}, source="forecast_service")
                AuditService(AuditLogRepository(session)).record(store_id=body.store_id, action="forecast_model_activated",
                    resource_type="forecast_model", resource_id=model.model_version_id,
                    after={"model_version": version, "status": "ready"}, source="forecast_service")
                session.commit()
            logger.info("forecast_training_completed request_id=%s store_id=%s model_version=%s duration=%.3f", request_id, body.store_id, version, time.monotonic()-started)
            return {"store_id": body.store_id, "model_version": version, "status": "ready", "trained_at": model.trained_at,
                    "history_start": model.history_start, "history_end": model.history_end,
                    "metrics": json.loads(model.metrics_json), "warnings": result.warnings}
        except Exception as exc:
            if staging.exists(): shutil.rmtree(staging, ignore_errors=True)
            self._mark_training_failed(body.store_id, version, exc)
            logger.exception("forecast_training_failed request_id=%s store_id=%s model_version=%s duration=%.3f", request_id, body.store_id, version, time.monotonic()-started)
            self._raise_core(exc, training=True)

    def _mark_training_failed(self, store_id, version, exc):
        with self.session_factory() as session:
            model = ForecastRepository(session).model(store_id, version)
            # A failed retrain must never demote the currently active ready model.
            if model is not None and not model.is_active:
                model.status = "failed"; model.warnings_json = _json([str(exc)]); model.updated_at = _now()
                session.commit()

    def predict(self, body, request_id=None):
        if body.forecast_horizon > self.settings.forecast_max_horizon:
            raise ForecastError("FORECAST_INPUT_INVALID", "forecast_horizon vượt quá giới hạn.", {"maximum": self.settings.forecast_max_horizon})
        history_days = body.history_days or self.settings.forecast_history_days
        with self.session_factory() as session:
            StoreRepository(session).get_required(body.store_id); repo = ForecastRepository(session)
            model = repo.model(body.store_id, body.model_version) if body.model_version else repo.active_model(body.store_id)
            if model is None or model.status != "ready": raise ModelNotReadyError(details={"store_id": body.store_id})
            version = model.model_version
            run = ForecastRunModel(forecast_run_id=str(uuid4()), store_id=body.store_id, cutoff_date=body.cutoff_date,
                horizon_days=body.forecast_horizon, quantiles_json="[0.25,0.5,0.75]", scope_json="{}",
                use_latest_calendar=True, status="running", engine_status="forecast_core", request_hash=hashlib.sha256(body.model_dump_json().encode()).hexdigest(),
                model_version=version, warnings_json="[]", created_at=_now())
            session.add(run); session.commit(); run_id = run.forecast_run_id
        started = time.monotonic(); logger.info("forecast_inference_started request_id=%s store_id=%s model_version=%s forecast_run_id=%s cutoff_date=%s forecast_horizon=%s", request_id, body.store_id, version, run_id, body.cutoff_date, body.forecast_horizon)
        try:
            artifact = self._artifact_dir(body.store_id, version)
            if not artifact.is_dir(): raise ForecastError("FORECAST_ARTIFACT_NOT_FOUND", "Không tìm thấy model artifacts.", {"model_version": version}, http_status=404)
            start = body.cutoff_date - timedelta(days=history_days - 1)
            with self.session_factory() as session:
                data = ForecastDataRepository(session); sales = data.sales_history(body.store_id, start, body.cutoff_date)
                calendar = data.calendar_features(body.store_id, start, body.cutoff_date + timedelta(days=body.forecast_horizon))
            package = predict_demand({"sales_history": sales, "calendar_features": calendar}, artifact,
                                     body.cutoff_date, body.forecast_horizon)
            with self.session_factory() as session:
                run = ForecastRepository(session).run(run_id)
                for p in package.predictions:
                    session.add(ForecastPredictionModel(prediction_id=str(uuid4()), forecast_run_id=run_id,
                        store_id=body.store_id, product_id=p.product_id, product_name=p.product_name,
                        target_date=p.target_date, horizon=p.horizon, p25=p.p25, p50=p.p50, p75=p.p75,
                        interval_lower=p.interval_lower, interval_upper=p.interval_upper, baseline_p50=p.baseline_p50,
                        calibration_source=p.calibration_source, warnings_json=_json(p.warnings), created_at=_now()))
                self._persist_realized_residuals(session, body.store_id, body.cutoff_date)
                run.status="completed"; run.engine_status="forecast_core"; run.warnings_json=_json(package.warnings); run.completed_at=_now()
                AuditService(AuditLogRepository(session)).record(store_id=body.store_id, action="forecast_generated",
                    resource_type="forecast_run", resource_id=run_id, after={"model_version": version, "predictions": len(package.predictions)}, source="forecast_service")
                session.commit()
            logger.info("forecast_inference_completed request_id=%s store_id=%s model_version=%s forecast_run_id=%s duration=%.3f", request_id, body.store_id, version, run_id, time.monotonic()-started)
            return self.get(run_id, body.store_id)
        except Exception as exc:
            with self.session_factory() as session:
                run = ForecastRepository(session).run(run_id)
                if run: run.status="failed"; run.failure_code=self._code(exc); run.failure_message=str(exc)[:500]; run.completed_at=_now(); session.commit()
            logger.exception("forecast_inference_failed request_id=%s store_id=%s model_version=%s forecast_run_id=%s duration=%.3f", request_id, body.store_id, version, run_id, time.monotonic()-started)
            self._raise_core(exc, training=False)

    @staticmethod
    def _persist_realized_residuals(session, store_id, as_of_date):
        """Persist only forecast/actual pairs that are already observable.

        This is intentionally invoked during normal forecast operation instead
        of fabricating history at decision time.
        """
        existing = select(ForecastResidualModel.forecast_run_id, ForecastResidualModel.product_id,
                          ForecastResidualModel.target_date, ForecastResidualModel.horizon)
        known = set(session.execute(existing).all())
        rows = session.execute(select(ForecastPredictionModel, ForecastRunModel, SalesDailyModel)
            .join(ForecastRunModel, ForecastRunModel.forecast_run_id == ForecastPredictionModel.forecast_run_id)
            .join(SalesDailyModel, (SalesDailyModel.store_id == ForecastPredictionModel.store_id) &
                  (SalesDailyModel.product_id == ForecastPredictionModel.product_id) &
                  (SalesDailyModel.date == ForecastPredictionModel.target_date))
            .where(ForecastPredictionModel.store_id == store_id, ForecastPredictionModel.target_date <= as_of_date,
                   ForecastRunModel.status == "completed")).all()
        for prediction, run, sale in rows:
            key=(prediction.forecast_run_id,prediction.product_id,prediction.target_date,prediction.horizon)
            if key not in known:
                session.add(ForecastResidualModel(residual_id=str(uuid4()), store_id=store_id, forecast_run_id=prediction.forecast_run_id,
                    product_id=prediction.product_id, target_date=prediction.target_date, horizon=prediction.horizon,
                    actual_value=sale.quantity, predicted_p25=prediction.p25, predicted_p50=prediction.p50, predicted_p75=prediction.p75,
                    residual=sale.quantity-prediction.p50, forecast_origin=run.cutoff_date, model_version=run.model_version, created_at=_now()))

    def create_legacy_run(self, store_id, body, idempotency_key=None, request_id=None):
        """Adapter for the canonical store-scoped frontend contract."""
        from app.schemas.forecast import ForecastPredictRequest
        if body.quantiles != [0.25, 0.5, 0.75]:
            raise ForecastError("FORECAST_INPUT_INVALID", "Forecast Core chỉ hỗ trợ P25/P50/P75.",
                                {"quantiles": body.quantiles})
        payload = body.model_dump(mode="json")
        endpoint = f"/api/v1/stores/{store_id}/forecast-runs"
        request_hash = canonical_hash(payload)
        if idempotency_key:
            with self.session_factory() as session:
                StoreRepository(session).get_required(store_id)
                replay = IdempotencyService(IdempotencyRepository(session)).register(
                    store_id=store_id, endpoint=endpoint, http_method="POST",
                    idempotency_key=idempotency_key, request_hash=request_hash)
                if replay.is_replay:
                    run_id = replay.record.resource_id
                    session.rollback()
                    if not run_id:
                        raise ForecastError("FORECAST_INFERENCE_FAILED", "Forecast request chưa hoàn tất.", http_status=409)
                    metadata = self.get_metadata(run_id, store_id)
                    if metadata["status"] == "blocked":
                        raise ModelNotReadyError(details={"forecast_run_id": run_id, "engine_status": metadata["engine_status"]})
                    return metadata
                session.commit()
        try:
            result = self.predict(ForecastPredictRequest(store_id=store_id, cutoff_date=body.cutoff_date,
                forecast_horizon=body.horizon_days), request_id)
            run_id = result["forecast_run_id"]
            with self.session_factory() as session:
                run = ForecastRepository(session).run(run_id)
                run.quantiles_json = _json(body.quantiles); run.scope_json = _json(body.scope)
                run.use_latest_calendar = body.use_latest_calendar
                if idempotency_key:
                    record = IdempotencyRepository(session).get(store_id=store_id, endpoint=endpoint,
                        http_method="POST", idempotency_key=idempotency_key)
                    record.resource_type = "forecast_run"; record.resource_id = run_id; record.response_status = 200
                session.commit()
            return self.get_metadata(run_id, store_id)
        except ModelNotReadyError as exc:
            run_id = str(uuid4())
            with self.session_factory() as session:
                session.add(ForecastRunModel(forecast_run_id=run_id, store_id=store_id,
                    cutoff_date=body.cutoff_date, horizon_days=body.horizon_days,
                    quantiles_json=_json(body.quantiles), scope_json=_json(body.scope),
                    use_latest_calendar=body.use_latest_calendar, status="blocked",
                    engine_status="model_unavailable", request_hash=request_hash,
                    failure_code="MODEL_NOT_READY", failure_message="Forecast model unavailable",
                    warnings_json="[]", created_at=_now(), completed_at=_now()))
                if idempotency_key:
                    record = IdempotencyRepository(session).get(store_id=store_id, endpoint=endpoint,
                        http_method="POST", idempotency_key=idempotency_key)
                    record.resource_type = "forecast_run"; record.resource_id = run_id; record.response_status = 503
                session.commit()
            raise ModelNotReadyError(details={"forecast_run_id": run_id, "engine_status": "model_unavailable"}) from exc

    def get_metadata(self, run_id: str, store_id: str):
        with self.session_factory() as session:
            run = ForecastRepository(session).run(run_id)
            if run is None or run.store_id != store_id:
                raise ForecastError("FORECAST_RUN_NOT_FOUND", "Không tìm thấy forecast run.",
                                    {"forecast_run_id": run_id}, http_status=404)
            return {"forecast_run_id": run.forecast_run_id, "store_id": run.store_id,
                "status": run.status, "engine_status": run.engine_status,
                "cutoff_date": run.cutoff_date, "horizon_days": run.horizon_days,
                "model_version": run.model_version,
                "warnings": json.loads(run.warnings_json or "[]"),
                "failure_code": run.failure_code, "failure_message": run.failure_message,
                "created_at": run.created_at, "completed_at": run.completed_at,
                "result_url": f"/api/v1/stores/{run.store_id}/forecast-runs/{run.forecast_run_id}/result"}

    def get_legacy_result(self, run_id: str, store_id: str):
        metadata = self.get_metadata(run_id, store_id)
        if metadata["status"] in {"blocked", "failed"}:
            code = metadata["failure_code"] or "FORECAST_INFERENCE_FAILED"
            if code == "MODEL_NOT_READY":
                raise ModelNotReadyError(details={"forecast_run_id": run_id, "engine_status": metadata["engine_status"]})
            raise ForecastError(code, metadata["failure_message"] or "Forecast inference thất bại.",
                                {"forecast_run_id": run_id}, http_status=500)
        if metadata["status"] == "running":
            return {**metadata, "predictions": []}
        with self.session_factory() as session:
            predictions = ForecastRepository(session).predictions(run_id)
        if not predictions:
            raise ForecastError("FORECAST_INFERENCE_FAILED", "Forecast completed nhưng không có predictions.",
                                {"forecast_run_id": run_id, "reason": "PERSISTENCE_INCONSISTENCY"}, http_status=500)
        return {**metadata, "forecast_date": metadata["cutoff_date"],
            "forecast_horizon": metadata["horizon_days"],
            "predictions": [self._prediction_dict(p) for p in predictions]}

    @staticmethod
    def _prediction_dict(p):
        return {"product_id": p.product_id, "product_name": p.product_name,
            "target_date": p.target_date, "horizon": p.horizon,
            "p25": float(p.p25), "p50": float(p.p50), "p75": float(p.p75),
            "interval_lower": float(p.interval_lower), "interval_upper": float(p.interval_upper),
            "baseline_p50": float(p.baseline_p50), "calibration_source": p.calibration_source,
            "warnings": json.loads(p.warnings_json or "[]")}

    def get(self, run_id: str, store_id: str | None = None):
        with self.session_factory() as session:
            repo=ForecastRepository(session); run=repo.run(run_id)
            if run is None or (store_id and run.store_id != store_id):
                raise ForecastError("FORECAST_RUN_NOT_FOUND", "Không tìm thấy forecast run.", {"forecast_run_id": run_id}, http_status=404)
            preds=repo.predictions(run_id)
            return {"forecast_run_id":run.forecast_run_id,"store_id":run.store_id,"forecast_date":run.cutoff_date,
                "forecast_horizon":run.horizon_days,"model_version":run.model_version or "","status":run.status,
                "warnings":json.loads(run.warnings_json or "[]"),"created_at":run.created_at,"completed_at":run.completed_at,
                "predictions":[{"product_id":p.product_id,"product_name":p.product_name,"target_date":p.target_date,
                    "horizon":p.horizon,"p25":float(p.p25),"p50":float(p.p50),"p75":float(p.p75),
                    "interval_lower":float(p.interval_lower),"interval_upper":float(p.interval_upper),
                    "baseline_p50":float(p.baseline_p50),"calibration_source":p.calibration_source,
                    "warnings":json.loads(p.warnings_json or "[]")} for p in preds]}

    @staticmethod
    def _code(exc):
        if isinstance(exc, ForecastError): return exc.code
        if isinstance(exc, FeatureTypeError): return "FORECAST_FEATURE_TYPE_INVALID"
        if isinstance(exc, ArtifactError): return "FORECAST_ARTIFACT_INVALID"
        if isinstance(exc, (DataValidationError, FeatureSchemaError, ValueError)): return "FORECAST_INPUT_INVALID"
        if isinstance(exc, InsufficientDataError): return "INSUFFICIENT_TRAINING_DATA"
        return "FORECAST_INFERENCE_FAILED"

    def _raise_core(self, exc, training):
        if isinstance(exc, (ForecastError, ModelNotReadyError, InsufficientTrainingDataError)): raise exc
        if isinstance(exc, InsufficientDataError): raise InsufficientTrainingDataError(details={"reason": str(exc)}) from exc
        if isinstance(exc, FeatureTypeError):
            raise ForecastError("FORECAST_FEATURE_TYPE_INVALID", "Forecast feature has an invalid numeric dtype.", {"reason": str(exc)}) from exc
        if isinstance(exc, ArtifactError): raise ForecastError("FORECAST_ARTIFACT_INVALID", "Model artifacts không hợp lệ.", {"reason": str(exc)}, http_status=500) from exc
        if isinstance(exc, (DataValidationError, FeatureSchemaError, ValueError)):
            code = "INSUFFICIENT_TRAINING_DATA" if training else "FORECAST_INPUT_INVALID"
            raise ForecastError(code, "Dữ liệu forecast không hợp lệ.", {"reason": str(exc)}) from exc
        code = "FORECAST_TRAINING_FAILED" if training else "FORECAST_INFERENCE_FAILED"
        raise ForecastError(code, "Huấn luyện forecast thất bại." if training else "Forecast inference thất bại.", http_status=500) from exc
