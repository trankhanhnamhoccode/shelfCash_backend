import json
from datetime import date, datetime, timezone

import pytest

from app.core.exceptions import PlanningError
from app.models.decision import DecisionRunModel
from app.services.decision.build_decision_brief import BuildDecisionBrief


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


def test_build_decision_brief_preserves_read_only_missing_artifact_fallback(client):
    package = {
        "decision_run_id": "brief-read-only",
        "store_id": "STORE_001",
        "status": "completed_with_no_feasible_recommendation",
        "recommended_strategy": None,
        "recommended_plan": {"items": []},
        "ingredient_demand": [],
        "business_metrics": {},
        "inventory_risk": {},
        "critic": {"findings": [], "warnings": ["NO_PLAN"]},
        "reason_codes": [],
        "warnings": [],
    }
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(_run("brief-read-only", package))
        session.commit()
        before = session.get(DecisionRunModel, "brief-read-only")
        before_snapshot = (before.package_json, before.created_at, before.completed_at)

    brief = BuildDecisionBrief(
        session_factory, client.app.state.settings, client.app.state.llm_provider,
    ).build("brief-read-only")

    assert brief.assistant_summary is not None
    assert brief.assistant_summary.source == "deterministic_fallback"
    assert brief.ingredient_synthesis == []
    with session_factory() as session:
        after = session.get(DecisionRunModel, "brief-read-only")
        assert (after.package_json, after.created_at, after.completed_at) == before_snapshot


def test_planning_facade_delegates_to_build_decision_brief(client, monkeypatch):
    expected = object()
    monkeypatch.setattr(BuildDecisionBrief, "build", lambda self, run_id: expected)

    assert client.app.state.decision_planning_service.get_decision_brief("delegated") is expected


def test_build_decision_brief_preserves_missing_run_error(client):
    with pytest.raises(PlanningError) as exc:
        BuildDecisionBrief(
            client.app.state.session_factory,
            client.app.state.settings,
            client.app.state.llm_provider,
        ).build("missing-decision")

    assert exc.value.code == "DECISION_RUN_NOT_FOUND"
    assert exc.value.http_status == 404
    assert exc.value.details == {"decision_run_id": "missing-decision"}


def test_brief_route_preserves_missing_run_error_envelope(client):
    response = client.get("/api/v1/decision-runs/missing-decision/brief")

    assert response.status_code == 404
    assert response.json()["code"] == "DECISION_RUN_NOT_FOUND"
    assert response.json()["details"] == {"decision_run_id": "missing-decision"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
