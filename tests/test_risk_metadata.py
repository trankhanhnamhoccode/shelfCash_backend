import json
from datetime import date, datetime, timezone

from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    IngredientDemandBrief,
    RecommendationBrief,
    RiskBrief,
)
from app.decision_intelligence.risk_metadata import project_risk_details
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.models.decision import DecisionRunModel


def _brief() -> DecisionBriefFacts:
    return DecisionBriefFacts(
        decision_run_id="risk-metadata-run",
        store_id="STORE_001",
        status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        ingredient_demand=[
            IngredientDemandBrief(
                ingredient_id="banana", ingredient_name="Chuoi", unit="kg",
                target_date=date(2026, 8, 21), p25=1, p50=2, p75=3,
            ),
            IngredientDemandBrief(
                ingredient_id="orange", ingredient_name="Cam", unit="kg",
                target_date=date(2026, 8, 21), p25=1, p50=2, p75=3,
            ),
        ],
        risk=RiskBrief(stockout_probability=None),
        critic=CriticBrief(),
        generated_at=datetime.now(timezone.utc),
    )


def _details(package: dict) -> list:
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, package)
    return project_risk_details(brief, facts)


def test_known_codes_have_deterministic_classification_and_business_text():
    details = _details({
        "warnings": ["RISK_METRIC_NOT_AVAILABLE", "CAPACITY_NOT_EVALUATED"],
        "critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"]},
    })
    by_code = {detail.code: detail for detail in details}

    assert by_code["STRESS_SHORTAGE_OBSERVED"].classification == "risk"
    assert by_code["RISK_METRIC_NOT_AVAILABLE"].classification == "limitation"
    capacity = by_code["CAPACITY_NOT_EVALUATED"]
    assert capacity.classification == "limitation"
    assert capacity.severity == "warning"
    assert capacity.title.encode("unicode_escape").decode() == "Ch\\u01b0a \\u0111\\xe1nh gi\\xe1 s\\u1ee9c ch\\u1ee9a kho"
    assert "CAPACITY_NOT_EVALUATED" not in " ".join(
        [capacity.title, capacity.meaning or "", capacity.recommended_action or ""]
    )
    assert "0%" not in " ".join(
        [by_code["RISK_METRIC_NOT_AVAILABLE"].title, by_code["RISK_METRIC_NOT_AVAILABLE"].meaning or ""]
    )


def test_stress_shortage_is_a_risk_without_inventing_probability():
    detail = _details({"critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"]}})[0]

    assert detail.classification == "risk"
    assert detail.category == "shortage"
    assert detail.severity == "warning"
    assert "xác suất" not in (detail.meaning or "").lower()


def test_duplicate_run_warning_codes_merge_evidence_but_entity_scoped_issues_do_not():
    merged = _details({
        "warnings": ["CAPACITY_NOT_EVALUATED"],
        "critic": {"warnings": ["CAPACITY_NOT_EVALUATED"]},
    })
    assert len(merged) == 1
    assert merged[0].source_count == 2
    assert merged[0].evidence_ids == [
        "source:package.critic.warnings", "source:package.warnings",
    ]

    ingredient_details = _details({
        "stress_tests": {"results": [{
            "scenario_id": "stress-1",
            "summary": {"by_key": [
                {"ingredient_id": "banana", "unit": "kg", "shortage_quantity": 1},
                {"ingredient_id": "orange", "unit": "kg", "shortage_quantity": 2},
            ]},
        }]},
    })
    shortage = [item for item in ingredient_details if item.code == "STRESS_SHORTAGE_OBSERVED"]
    assert [(item.ingredient_id, item.ingredient_name) for item in shortage] == [
        ("banana", "Chuoi"), ("orange", "Cam"),
    ]


def test_unknown_codes_remain_safe_and_ordering_is_stable():
    package = {
        "warnings": ["Z_NEW_WARNING", "CAPACITY_NOT_EVALUATED"],
        "critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"]},
    }
    first = _details(package)
    second = _details({
        "warnings": list(reversed(package["warnings"])),
        "critic": {"warnings": list(reversed(package["critic"]["warnings"]))},
    })
    unknown = next(item for item in first if item.code == "Z_NEW_WARNING")

    assert unknown.classification == "unknown"
    assert unknown.category == "unknown"
    assert unknown.meaning is None
    assert unknown.recommended_action is None
    assert [item.code for item in first] == [item.code for item in second]
    assert [item.code for item in first] == [
        "STRESS_SHORTAGE_OBSERVED", "CAPACITY_NOT_EVALUATED", "Z_NEW_WARNING",
    ]


def _old_run(package: dict) -> DecisionRunModel:
    return DecisionRunModel(
        decision_run_id="phase4-old-run", store_id="STORE_001",
        forecast_run_id="missing-forecast", as_of_date=date(2026, 8, 20),
        horizon_days=7, engine_mode="deterministic", status=package["status"],
        scenario_method="test", scenario_count=1, random_seed=42,
        recommended_strategy=package.get("recommended_strategy"), request_json="{}",
        package_json=json.dumps(package), warnings_json="[]",
        created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )


def test_old_run_projects_risk_details_read_only_without_llm(client):
    package = {
        "decision_run_id": "phase4-old-run", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [], "business_metrics": {}, "inventory_risk": {},
        "critic": {"findings": [], "warnings": ["CAPACITY_NOT_EVALUATED"]},
        "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_old_run(package))
        session.commit()

    response = client.get("/api/v1/decision-runs/phase4-old-run/brief")
    assert response.status_code == 200
    details = response.json()["risk_details"]
    assert details[0]["code"] == "CAPACITY_NOT_EVALUATED"
    assert details[0]["classification"] == "limitation"
    assert "assistant" not in client.get("/api/v1/decision-runs/phase4-old-run").json()


def test_no_feasible_run_keeps_explicit_critic_detail():
    details = _details({
        "critic": {"findings": [{
            "code": "CAPACITY_CONSEQUENCE", "severity": "error",
        }], "warnings": []},
    })

    assert len(details) == 1
    assert details[0].code == "CAPACITY_CONSEQUENCE"
    assert details[0].classification == "risk"
    assert details[0].severity == "critical"
