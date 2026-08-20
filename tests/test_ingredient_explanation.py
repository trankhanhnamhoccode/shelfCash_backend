import json
from datetime import date, datetime, timezone

import pytest

from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    IngredientDemandBrief,
    ProcurementRowBrief,
    RecommendationBrief,
    RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.models.decision import DecisionRunModel
from app.schemas.decision import ExplanationRequest


def _brief(*, available=True, banana_order=True) -> DecisionBriefFacts:
    rows = [
        ProcurementRowBrief(
            ingredient_id="banana", ingredient_name="Banana", quantity=30,
            unit="kg", pack_count=6, pack_size=5,
            reason_codes=["PACK_SIZE_ROUNDING"],
        ),
        ProcurementRowBrief(ingredient_id="orange", ingredient_name="Orange", quantity=25, unit="kg"),
    ] if banana_order else [ProcurementRowBrief(ingredient_id="orange", ingredient_name="Orange", quantity=25, unit="kg")]
    return DecisionBriefFacts(
        decision_run_id="ingredient-run", store_id="STORE_001",
        status="completed" if available else "completed_with_no_feasible_recommendation",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=available, strategy="balanced" if available else None),
        procurement_rows=rows if available else [],
        ingredient_demand=[
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 21), p25=8, p50=9.5, p75=10),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 22), p25=9, p50=10, p75=11),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 23), p25=8, p50=9.45, p75=10.5),
            IngredientDemandBrief(ingredient_id="orange", ingredient_name="Orange", unit="kg", target_date=date(2026, 8, 21), p25=7, p50=8, p75=9),
        ],
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _package():
    return {
        "inventory_risk": {
            "results": [{
                "scenario_id": "baseline",
                "summary": {"by_key": [
                    {"ingredient_id": "banana", "unit": "kg", "total_demand": 28.95, "fulfilled_quantity": 20, "shortage_quantity": 8.95, "ending_inventory": 0, "fill_rate": 0.69},
                    {"ingredient_id": "orange", "unit": "kg", "total_demand": 8, "fulfilled_quantity": 8, "shortage_quantity": 0, "ending_inventory": 0, "fill_rate": 1},
                ]},
            }],
        },
    }


class _Gateway:
    available = True

    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
        self.payloads = []

    async def generate_json(self, _system, payload, **_kwargs):
        self.calls += 1
        self.payloads.append(payload)
        return self.factory(payload)


def _valid_observational_response(payload):
    by_type = {item["type"]: item for item in payload["evidence"]}
    demand = by_type["DEMAND_HORIZON_SUMMARY"]
    alignment = by_type["DEMAND_ORDER_ALIGNMENT"]
    return {
        "answer": "Banana median demand is 28.95 kg. The planned quantity is 30 kg, 1.05 kg above that total.",
        "claims": [
            {"type": "DEMAND_HORIZON_SUMMARY", "text": "Banana median demand is 28.95 kg.", "evidence_ids": [demand["evidence_id"]]},
            {"type": "DEMAND_ORDER_ALIGNMENT", "text": "The planned quantity is 30 kg, 1.05 kg above that total.", "evidence_ids": [alignment["evidence_id"]]},
        ],
        "used_evidence_ids": [demand["evidence_id"], alignment["evidence_id"]],
    }


def _explain(factory, question="Why order Banana?"):
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    gateway = _Gateway(factory)
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question=question, language="en", detail_level="simple",
        semantic_facts=facts, ingredient_id="banana",
    )
    return response, gateway


def test_optional_ingredient_id_preserves_question_only_request_contract():
    assert ExplanationRequest(question="Why this plan?").ingredient_id is None
    assert ExplanationRequest(question="Why Banana?", ingredient_id="banana").ingredient_id == "banana"


def test_explicit_target_scopes_qwen_evidence_and_question_id_conflict_keeps_target():
    response, gateway = _explain(_valid_observational_response, question="Why does Orange need ordering?")

    assert response.source == "openrouter_qwen"
    assert response.entities["ingredient_ids"] == ["banana"]
    assert gateway.calls == 1
    assert gateway.payloads[0]["target"] == {"ingredient_name": "Banana", "scope": "one_ingredient_only"}
    assert all(
        item.get("ingredient_id") in {None, "banana"}
        for item in gateway.payloads[0]["evidence"]
    )
    assert not any(item.get("ingredient_id") == "orange" for item in gateway.payloads[0]["evidence"])
    assert not any(item["type"] == "DEMAND_DAILY" for item in gateway.payloads[0]["evidence"])


def test_daily_rows_are_sent_only_for_an_explicit_daily_question():
    _, gateway = _explain(_valid_observational_response, question="Which Banana day has peak demand?")
    assert any(item["type"] == "DEMAND_DAILY" for item in gateway.payloads[0]["evidence"])


def test_false_pack_causality_and_entity_switch_are_rejected_to_targeted_fallback():
    def pack_claim(payload):
        demand = next(item for item in payload["evidence"] if item["type"] == "DEMAND_HORIZON_SUMMARY")
        return {
            "answer": "Banana is ordered because of pack size.",
            "claims": [{"type": "DEMAND_HORIZON_SUMMARY", "text": "Banana is ordered because of pack size.", "evidence_ids": [demand["evidence_id"]]}],
            "used_evidence_ids": [demand["evidence_id"]],
        }

    rejected, _ = _explain(pack_claim, question="Why does a 5 kg pack make the order 30 kg?")
    assert rejected.provider == "deterministic_fallback"
    assert "pack" not in rejected.answer.lower()

    def switched(payload):
        demand = next(item for item in payload["evidence"] if item["type"] == "DEMAND_HORIZON_SUMMARY")
        return {
            "answer": "Orange needs ordering.",
            "claims": [{"type": "DEMAND_HORIZON_SUMMARY", "text": "Orange needs ordering.", "evidence_ids": [demand["evidence_id"]]}],
            "used_evidence_ids": [demand["evidence_id"]],
        }

    switched_response, _ = _explain(switched)
    assert switched_response.provider == "deterministic_fallback"
    assert "Orange" not in switched_response.answer


def test_raw_machine_codes_are_rejected_from_user_facing_narrative():
    def raw_code(payload):
        demand = next(item for item in payload["evidence"] if item["type"] == "DEMAND_HORIZON_SUMMARY")
        return {
            "answer": "PACK_SIZE_ROUNDING applies to Banana.",
            "claims": [{"type": "DEMAND_HORIZON_SUMMARY", "text": "PACK_SIZE_ROUNDING applies to Banana.", "evidence_ids": [demand["evidence_id"]]}],
            "used_evidence_ids": [demand["evidence_id"]],
        }

    response, _ = _explain(raw_code)
    assert response.provider == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in response.answer


def test_baseline_consequence_is_allowed_but_inbound_contradiction_is_rejected():
    def valid_baseline(payload):
        baseline = next(item for item in payload["evidence"] if item["type"] == "NO_PLANNED_PURCHASE_BASELINE")
        return {
            "answer": "With planned purchases excluded, simulation shows a shortage of 8.95 kg.",
            "claims": [{"type": "NO_PLANNED_PURCHASE_BASELINE", "text": "With planned purchases excluded, simulation shows a shortage of 8.95 kg.", "evidence_ids": [baseline["evidence_id"]]}],
            "used_evidence_ids": [baseline["evidence_id"]],
        }

    valid, _ = _explain(valid_baseline, question="What if no new Banana purchase is added?")
    assert valid.source == "openrouter_qwen"

    def contradicted_baseline(payload):
        baseline = next(item for item in payload["evidence"] if item["type"] == "NO_PLANNED_PURCHASE_BASELINE")
        return {
            "answer": "There is no inbound stock.",
            "claims": [{"type": "NO_PLANNED_PURCHASE_BASELINE", "text": "There is no inbound stock.", "evidence_ids": [baseline["evidence_id"]]}],
            "used_evidence_ids": [baseline["evidence_id"]],
        }

    rejected, _ = _explain(contradicted_baseline)
    assert rejected.provider == "deterministic_fallback"


def test_malformed_and_timeout_qwen_use_useful_targeted_fallback():
    malformed, _ = _explain(lambda _payload: {"bad": "schema"})
    assert malformed.provider == "deterministic_fallback"
    assert "Banana" in malformed.answer
    assert "30" in malformed.answer

    def timeout(_payload):
        raise TimeoutError("mock timeout")

    timed_out, _ = _explain(timeout)
    assert timed_out.provider == "deterministic_fallback"
    assert "Banana" in timed_out.answer


def test_sparse_demand_only_and_no_feasible_fallback_never_invent_purchase():
    demand_only = _brief(banana_order=False)
    demand_facts = DecisionSemanticEvidenceBuilder().build(demand_only, _package())
    response = DecisionNarrativeProvider(None, None).explain(
        demand_only, question="Explain Banana", language="en", detail_level="simple",
        semantic_facts=demand_facts, ingredient_id="banana",
    )
    assert "Median demand" in response.answer
    assert "No purchase quantity" in response.answer

    no_feasible = _brief(available=False)
    response = DecisionNarrativeProvider(None, None).explain(
        no_feasible, question="Explain Banana", language="en", detail_level="simple",
        semantic_facts=DecisionSemanticEvidenceBuilder().build(no_feasible, _package()), ingredient_id="banana",
    )
    assert "No feasible purchase quantity" in response.answer
    assert "30" not in response.answer


def _run(run_id: str, package: dict) -> DecisionRunModel:
    return DecisionRunModel(
        decision_run_id=run_id, store_id="STORE_001", forecast_run_id="missing-forecast",
        as_of_date=date(2026, 8, 20), horizon_days=7, engine_mode="deterministic",
        status="completed", scenario_method="test", scenario_count=1, random_seed=42,
        recommended_strategy="balanced", request_json="{}", package_json=json.dumps(package),
        warnings_json="[]", created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )


def test_api_backward_compatibility_and_invalid_target_are_deterministic(client):
    package = {
        "decision_run_id": "ingredient-api", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [{"ingredient_id": "banana", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3}],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("ingredient-api", package))
        session.commit()

    legacy = client.post("/api/v1/decision-runs/ingredient-api/explanation", json={"language": "en", "detail_level": "simple", "question": "Why this plan?"})
    assert legacy.status_code == 200
    targeted = client.post("/api/v1/decision-runs/ingredient-api/explanation", json={"language": "en", "detail_level": "simple", "question": "Explain Banana", "ingredient_id": "banana"})
    assert targeted.status_code == 200
    assert targeted.json()["entities"]["ingredient_ids"] == ["banana"]
    invalid = client.post("/api/v1/decision-runs/ingredient-api/explanation", json={"ingredient_id": "orange"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "DECISION_RUN_INGREDIENT_NOT_FOUND"
