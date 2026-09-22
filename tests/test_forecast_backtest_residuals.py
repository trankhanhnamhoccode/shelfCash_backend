from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from app.core.exceptions import InsufficientTrainingDataError
from app.forecasting.contracts import ForecastPrediction, ForecastPredictionResult, ForecastTrainingResult
from app.models.business import ProductModel, SalesDailyModel
from app.models.operations import ForecastPredictionModel, ForecastResidualModel, ForecastRunModel


class RecordingBacktestProvider:
    name = "recording-backtest"

    def __init__(self, *, fail_origin=None):
        self.calls = []
        self.fail_origin = fail_origin

    def train(self, canonical_data, artifact_directory, *, config, model_version, debug_export=None):
        cutoff = max(canonical_data["sales_history"]["date"])
        self.calls.append(("train", cutoff, max(canonical_data["sales_history"]["date"])))
        if cutoff == self.fail_origin:
            raise InsufficientTrainingDataError(details={"cutoff": cutoff.isoformat()})
        artifact_directory.mkdir(parents=True, exist_ok=True)
        return ForecastTrainingResult(model_version, artifact_directory, {}, {}, {}, {}, ())

    def predict(self, canonical_data, artifact_directory, cutoff_date, forecast_horizon, *, debug_export=None):
        self.calls.append(("predict", cutoff_date, max(canonical_data["sales_history"]["date"])))
        predictions = tuple(
            ForecastPrediction(
                store_id="STORE_001", product_id="backtest-product", product_name="Backtest product",
                unit="portion", target_date=cutoff_date.fromordinal(cutoff_date.toordinal() + horizon), horizon=horizon,
                p25=8, p50=10, p75=12, interval_lower=8, interval_upper=12,
                baseline_p50=10, calibration_source="test", warnings=(),
            )
            for horizon in range(1, forecast_horizon + 1)
        )
        return ForecastPredictionResult(cutoff_date, forecast_horizon, "backtest-v1", predictions, ())


def _sales(client, *, end=date(2026, 1, 10)):
    with client.app.state.session_factory() as session:
        session.add(ProductModel(
            product_id="backtest-product", store_id="STORE_001", product="Backtest product",
            normalized_name="backtest product", active=True, source="test",
        ))
        current = date(2026, 1, 1)
        while current <= end:
            # Values after early origins are intentionally extreme; the fake
            # provider must never receive them for those origins.
            quantity = Decimal("999") if current >= date(2026, 1, 6) else Decimal("20")
            session.add(SalesDailyModel(
                sales_record_id=str(uuid4()), store_id="STORE_001", date=current,
                product_id="backtest-product", quantity=quantity, promotion=False, source="test",
            ))
            current = current.fromordinal(current.toordinal() + 1)
        session.commit()


def _body(**overrides):
    body = {
        "store_id": "STORE_001", "origin_date_from": "2026-01-03", "origin_date_to": "2026-01-05",
        "origin_frequency": "daily", "forecast_horizon": 2, "history_days": 3,
        "model_version": "backtest-v1",
    }
    body.update(overrides)
    return body


def _admin(client, body):
    client.app.state.settings.shelfcash_admin_api_key = "admin-secret"
    return client.post("/api/v1/admin/forecast-models/backtest-residuals", headers={"X-ShelfCash-Admin-Key": "admin-secret"}, json=body)


def test_backtest_is_admin_only_and_has_typed_openapi_contract(client):
    client.app.state.settings.shelfcash_api_key = "normal-secret"
    client.app.state.settings.shelfcash_admin_api_key = "admin-secret"
    normal = client.post("/api/v1/admin/forecast-models/backtest-residuals", headers={"X-ShelfCash-Key": "normal-secret"}, json=_body())
    assert normal.status_code == 403 and normal.json()["code"] == "admin_forbidden"
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/admin/forecast-models/backtest-residuals"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("ForecastBacktestResidualRequest")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("ForecastBacktestResidualResponse")
    assert any(parameter["name"] == "x-shelfcash-admin-key" for parameter in operation["parameters"])
    assert operation["responses"]["403"]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")


def test_backtest_persists_chronological_residuals_and_is_idempotent(client):
    _sales(client)
    provider = RecordingBacktestProvider()
    client.app.state.forecast_service.production_provider = provider

    response = _admin(client, _body())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["origins_requested"] == 3
    assert payload["origins_completed"] == 3
    assert payload["residuals_created"] == 6
    assert payload["residuals_missing_actual"] == 0
    assert payload["stochastic_ready"] is True
    assert {(item["horizon"], item["residual_count"], item["ready"]) for item in payload["coverage"]} == {(1, 3, True), (2, 3, True)}
    # Every training/inference call sees sales no later than its own origin.
    assert all(origin == visible_end for _, origin, visible_end in provider.calls)

    with client.app.state.session_factory() as session:
        residuals = session.scalars(select(ForecastResidualModel).order_by(ForecastResidualModel.forecast_origin, ForecastResidualModel.horizon)).all()
        assert len(residuals) == 6
        assert all(item.target_date > item.forecast_origin for item in residuals)
        assert all(item.target_date.fromordinal(item.forecast_origin.toordinal() + item.horizon) == item.target_date for item in residuals)
        # For 03/01 target 04/01, actual is 20 and p50 is 10.
        first = next(item for item in residuals if item.forecast_origin == date(2026, 1, 3) and item.horizon == 1)
        assert first.residual == Decimal("10.000000")
        assert session.scalar(select(func.count()).select_from(ForecastRunModel)) == 3
        assert session.scalar(select(func.count()).select_from(ForecastPredictionModel)) == 6

    rerun = _admin(client, _body())
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["residuals_created"] == 0
    assert rerun.json()["residuals_unchanged"] == 6
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ForecastResidualModel)) == 6


def test_backtest_skips_missing_actual_and_insufficient_training_origin(client):
    _sales(client, end=date(2026, 1, 5))
    provider = RecordingBacktestProvider(fail_origin=date(2026, 1, 3))
    client.app.state.forecast_service.production_provider = provider

    response = _admin(client, _body(origin_date_from="2026-01-03", origin_date_to="2026-01-05"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["origins_skipped"] == 1
    # 04/01 has one actual target (05/01); 05/01 has none.
    assert payload["residuals_created"] == 1
    assert payload["residuals_missing_actual"] == 3
    with client.app.state.session_factory() as session:
        rows = list(session.scalars(select(ForecastResidualModel)))
        assert len(rows) == 1
        assert rows[0].forecast_origin == date(2026, 1, 4)
