import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.models.business import ProductModel, SalesDailyModel
from app.models.operations import ForecastModelVersionModel
from app.models.operations import ForecastRunModel


def test_forecast_auth_validation_and_not_found(client):
    client.app.state.settings.shelfcash_api_key = "secret"
    assert client.post("/api/v1/forecasts", json={}).status_code == 401
    headers={"X-ShelfCash-Key":"secret"}
    invalid=client.post("/api/v1/forecasts", headers=headers, json={"store_id":"STORE_001","cutoff_date":"2026-08-03","forecast_horizon":8})
    assert invalid.status_code == 422
    missing=client.get(f"/api/v1/forecasts/{uuid4()}", headers=headers)
    assert missing.status_code == 404 and missing.json()["code"] == "FORECAST_RUN_NOT_FOUND"


def test_predict_model_not_ready(client):
    response=client.post("/api/v1/forecasts", json={"store_id":"STORE_001","cutoff_date":"2026-08-03","forecast_horizon":7})
    assert response.status_code == 503 and response.json()["code"] == "MODEL_NOT_READY"
    absent=client.post("/api/v1/forecasts", json={"store_id":"NO_STORE","cutoff_date":"2026-08-03","forecast_horizon":1})
    assert absent.status_code == 404 and absent.json()["code"] == "STORE_NOT_FOUND"


def test_train_endpoint_success_contract(client, monkeypatch):
    now=datetime.now(timezone.utc)
    monkeypatch.setattr(client.app.state.forecast_service,"train",lambda body, request_id=None:{
        "store_id":body.store_id,"model_version":body.model_version,"status":"ready","trained_at":now,
        "history_start":date(2026,1,1),"history_end":body.cutoff_date,"metrics":{},"warnings":[]})
    response=client.post("/api/v1/forecast-models/train",json={"store_id":"STORE_001","cutoff_date":"2026-08-03","model_version":"v-api","history_days":200})
    assert response.status_code == 200 and response.json()["status"] == "ready"


def test_predict_artifact_missing_marks_run_failed(client):
    sf=client.app.state.session_factory
    with sf() as s:
        s.add(ForecastModelVersionModel(model_version_id=str(uuid4()),store_id="STORE_001",model_version="missing-v1",
            artifact_key="STORE_001/missing-v1",status="ready",is_active=True,created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        s.commit()
    response=client.post("/api/v1/forecasts",json={"store_id":"STORE_001","cutoff_date":"2026-08-03","forecast_horizon":1})
    assert response.status_code == 404 and response.json()["code"] == "FORECAST_ARTIFACT_NOT_FOUND"
    with sf() as s:
        run=s.query(ForecastRunModel).one()
        assert run.status == "failed" and run.failure_code == "FORECAST_ARTIFACT_NOT_FOUND"


def test_failed_training_keeps_old_active_model(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:
        product=ProductModel(product_id="training-product",store_id="STORE_001",product="Coffee",normalized_name="coffee",active=True,source="test")
        s.add(product)
        s.add(SalesDailyModel(sales_record_id=str(uuid4()),store_id="STORE_001",date=date(2026,8,3),product_id=product.product_id,quantity=Decimal("3"),promotion=False,source="test"))
        s.add(ForecastModelVersionModel(model_version_id=str(uuid4()),store_id="STORE_001",model_version="old-v1",
            artifact_key="STORE_001/old-v1",status="ready",is_active=True,created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        s.commit()
    def fail(*args,**kwargs): raise RuntimeError("training exploded")
    monkeypatch.setattr("app.services.forecast_service.train_forecast_core",fail)
    response=client.post("/api/v1/forecast-models/train",json={"store_id":"STORE_001","cutoff_date":"2026-08-03","model_version":"new-v1","history_days":1})
    assert response.status_code == 500 and response.json()["code"] == "FORECAST_TRAINING_FAILED"
    with sf() as s:
        old=s.query(ForecastModelVersionModel).filter_by(model_version="old-v1").one()
        new=s.query(ForecastModelVersionModel).filter_by(model_version="new-v1").one()
        assert old.is_active and old.status == "ready"
        assert not new.is_active and new.status == "failed"


def test_legacy_artifact_missing_ownership_and_empty_completed_result(client):
    sf=client.app.state.session_factory; now=datetime.now(timezone.utc)
    with sf() as s:
        s.add(ForecastModelVersionModel(model_version_id=str(uuid4()),store_id="STORE_001",model_version="legacy-missing",
            artifact_key="STORE_001/legacy-missing",status="ready",is_active=True,created_at=now,updated_at=now))
        empty_id=str(uuid4())
        s.add(ForecastRunModel(forecast_run_id=empty_id,store_id="STORE_001",cutoff_date=date(2026,8,3),
            horizon_days=1,quantiles_json="[0.25,0.5,0.75]",scope_json="{}",use_latest_calendar=True,
            status="completed",engine_status="forecast_core",request_hash="empty",model_version="legacy-missing",
            warnings_json="[]",created_at=now,completed_at=now))
        s.commit()
    body={"cutoff_date":"2026-08-03","horizon_days":1,"quantiles":[0.25,0.5,0.75],"scope":{},"use_latest_calendar":True}
    missing=client.post("/api/v1/stores/STORE_001/forecast-runs",json=body)
    assert missing.status_code == 404 and missing.json()["code"] == "FORECAST_ARTIFACT_NOT_FOUND"
    wrong_store=client.get(f"/api/v1/stores/STORE_TEST_001/forecast-runs/{empty_id}")
    assert wrong_store.status_code == 404 and wrong_store.json()["code"] == "FORECAST_RUN_NOT_FOUND"
    inconsistent=client.get(f"/api/v1/stores/STORE_001/forecast-runs/{empty_id}/result")
    assert inconsistent.status_code == 500 and inconsistent.json()["details"]["reason"] == "PERSISTENCE_INCONSISTENCY"


def test_predict_persists_and_get_does_not_infer(client, monkeypatch):
    from shelfcash_core.contracts import ForecastPackage, ForecastPrediction
    sf=client.app.state.session_factory; root=client.app.state.settings.forecast_artifact_root
    artifact=root/"STORE_001"/"v1"; artifact.mkdir(parents=True, exist_ok=True)
    with sf() as s:
        product=ProductModel(product_id="forecast-product",store_id="STORE_001",product="Coffee",normalized_name="coffee",active=True,source="test")
        s.add(product)
        for day in range(1,4):
            s.add(SalesDailyModel(sales_record_id=str(uuid4()),store_id="STORE_001",date=date(2026,8,day),product_id=product.product_id,quantity=Decimal("3"),promotion=False,source="test"))
        s.add(ForecastModelVersionModel(model_version_id=str(uuid4()),store_id="STORE_001",model_version="v1",
            artifact_key="STORE_001/v1",status="ready",is_active=True,created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc)))
        s.commit()
    calls=[]
    def fake_predict(*args, **kwargs):
        calls.append(1)
        return ForecastPackage(forecast_date=date(2026,8,3),forecast_horizon=1,model_version="v1",predictions=[
            ForecastPrediction(store_id="STORE_001",product_id="forecast-product",product_name="Coffee",target_date=date(2026,8,4),horizon=1,
                p25=1,p50=2,p75=3,interval_lower=1,interval_upper=3,baseline_p50=2,calibration_source="global")],warnings=["TEST_WARNING"])
    monkeypatch.setattr("app.services.forecast_service.predict_demand",fake_predict)
    response=client.post("/api/v1/forecasts",json={"store_id":"STORE_001","cutoff_date":"2026-08-03","forecast_horizon":1,"history_days":3})
    assert response.status_code == 201, response.text
    payload=response.json(); assert payload["predictions"][0]["product_id"] == "forecast-product"
    fetched=client.get(f"/api/v1/forecasts/{payload['forecast_run_id']}")
    assert fetched.status_code == 200 and fetched.json() == payload and len(calls) == 1
