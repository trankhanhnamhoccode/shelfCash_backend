import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import PlanningError
from app.models.decision import DecisionRunModel
from app.services.decision.build_decision_brief import BuildDecisionBrief
from app.services.decision.explain_decision import ExplainDecision
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


def _use_case(client):
    session_factory = client.app.state.session_factory
    return ExplainDecision(
        BuildDecisionBrief(
            session_factory,
            client.app.state.settings,
            client.app.state.llm_provider,
        ),
        ReadDecisionPackage(session_factory),
        client.app.state.settings,
        client.app.state.llm_provider,
    )


def test_explain_decision_preserves_read_only_fallback(client):
    package = {
        "decision_run_id": "explain-read-only",
        "store_id": "STORE_001",
        "status": "completed_with_no_feasible_recommendation",
        "recommended_strategy": None,
        "recommended_plan": {"items": []},
        "ingredient_demand": [],
        "business_metrics": {},
        "inventory_risk": {},
        "critic": {"findings": [], "warnings": ["NO_PLAN"]},
        "reason_codes": [],
        "warnings": ["NO_PLAN"],
    }
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(_run("explain-read-only", package))
        session.commit()
        before = session.get(DecisionRunModel, "explain-read-only")
        snapshot = (before.package_json, before.created_at, before.completed_at)

    response = _use_case(client).explain(
        "explain-read-only",
        SimpleNamespace(ingredient_id=None, question="Why?", language="en", detail_level="simple"),
    )

    assert response["decision_run_id"] == "explain-read-only"
    with session_factory() as session:
        after = session.get(DecisionRunModel, "explain-read-only")
        assert (after.package_json, after.created_at, after.completed_at) == snapshot


def test_planning_facade_delegates_to_explain_decision(client, monkeypatch):
    expected = {"source": "delegated"}
    monkeypatch.setattr(ExplainDecision, "explain", lambda self, run_id, body: expected)

    result = client.app.state.decision_planning_service.explain_decision(
        "delegated", SimpleNamespace(),
    )

    assert result is expected


def test_explain_decision_preserves_missing_run_error(client):
    with pytest.raises(PlanningError) as exc:
        _use_case(client).explain(
            "missing-decision",
            SimpleNamespace(ingredient_id="ingredient", question=None, language="en", detail_level="simple"),
        )

    assert exc.value.code == "DECISION_RUN_NOT_FOUND"
    assert exc.value.http_status == 404
    assert exc.value.details == {"decision_run_id": "missing-decision"}


def test_explanation_route_preserves_missing_run_error_envelope(client):
    response = client.post(
        "/api/v1/decision-runs/missing-decision/explanation",
        json={"language": "en", "detail_level": "simple"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "DECISION_RUN_NOT_FOUND"
    assert response.json()["details"] == {"decision_run_id": "missing-decision"}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
