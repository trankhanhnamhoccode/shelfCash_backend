import json
from datetime import date, datetime, timezone

import pytest

from app.core.exceptions import PlanningError
from app.models.decision import DecisionRunModel
from app.services.decision.read_decision_package import ReadDecisionPackage


def _run(run_id: str, package: dict) -> DecisionRunModel:
    return DecisionRunModel(
        decision_run_id=run_id,
        store_id="STORE_001",
        forecast_run_id="missing-forecast",
        as_of_date=date(2026, 8, 19),
        horizon_days=7,
        engine_mode="deterministic",
        status=package["status"],
        scenario_method="test",
        scenario_count=1,
        random_seed=42,
        recommended_strategy=package.get("recommended_strategy"),
        request_json="{}",
        package_json=json.dumps(package),
        warnings_json="[]",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def test_reader_and_planning_facade_return_same_package_without_writes(client):
    package = {
        "decision_run_id": "read-package",
        "store_id": "STORE_001",
        "status": "completed",
        "recommended_strategy": "balanced",
        "recommended_plan": {"items": []},
        "assistant": {"overall_summary": {"source": "rule_based"}},
        "warnings": ["WATCH"],
        "business_metrics": {"fill_rate": 1.0},
    }
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(_run("read-package", package))
        session.commit()
        before = session.get(DecisionRunModel, "read-package")
        before_snapshot = (before.package_json, before.created_at, before.completed_at)

    reader = ReadDecisionPackage(session_factory)
    assert reader.read("read-package") == package
    assert client.app.state.decision_planning_service.get_decision("read-package") == package

    with session_factory() as session:
        after = session.get(DecisionRunModel, "read-package")
        assert (after.package_json, after.created_at, after.completed_at) == before_snapshot


def test_planning_facade_delegates_to_read_decision_package(client, monkeypatch):
    service = client.app.state.decision_planning_service
    expected = {"decision_run_id": "delegated"}
    monkeypatch.setattr(service.read_decision_package, "read", lambda run_id: expected)

    assert service.get_decision("delegated") is expected


def test_reader_preserves_missing_decision_run_error(client):
    with pytest.raises(PlanningError) as exc:
        ReadDecisionPackage(client.app.state.session_factory).read("missing-decision")

    assert exc.value.code == "DECISION_RUN_NOT_FOUND"
    assert exc.value.http_status == 404
    assert exc.value.details == {"decision_run_id": "missing-decision"}


def test_decision_read_route_preserves_missing_run_error_envelope(client):
    response = client.get("/api/v1/decision-runs/missing-decision")

    assert response.status_code == 404
    assert response.json()["code"] == "DECISION_RUN_NOT_FOUND"
    assert response.json()["details"] == {"decision_run_id": "missing-decision"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
