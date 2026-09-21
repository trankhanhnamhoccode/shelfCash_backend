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
from app.models.business import IngredientModel
from app.models.business import IngredientAliasModel
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


def test_api_validates_id_against_persisted_demand_plan_universe_without_catalog(client):
    package = {
        "decision_run_id": "ingredient-plan-only", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced",
        "recommended_plan": {"items": [{"ingredient_id": "plan-only", "order_quantity": 3, "unit": "kg"}]},
        "ingredient_demand": [],
        "inventory_risk": {"ingredient_id": "risk-only"},
        "assistant": {"ingredient_synthesis": [{"ingredient_id": "synthesis-only"}]},
        "business_metrics": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(
            ingredient_id="catalog-only", store_id="STORE_001", ingredient="Catalog only",
            normalized_name="catalog only", base_unit="kg", active=True, source="test",
        ))
        session.add(_run("ingredient-plan-only", package))
        session.commit()

    valid = client.post(
        "/api/v1/decision-runs/ingredient-plan-only/explanation",
        json={"language": "en", "detail_level": "simple", "question": "Why?", "ingredient_id": "plan-only"},
    )
    assert valid.status_code == 200
    assert valid.json()["entities"]["ingredient_ids"] == ["plan-only"]
    with client.app.state.session_factory() as session:
        assert session.get(DecisionRunModel, "ingredient-plan-only").package_json == json.dumps(package)

    for absent_id in ("risk-only", "synthesis-only", "catalog-only"):
        invalid = client.post(
            "/api/v1/decision-runs/ingredient-plan-only/explanation",
            json={"ingredient_id": absent_id},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "DECISION_RUN_INGREDIENT_NOT_FOUND"
        assert invalid.json()["details"] == {
            "decision_run_id": "ingredient-plan-only", "ingredient_id": absent_id,
        }


def test_question_resolver_scopes_evidence_and_detects_only_confident_mismatch(client):
    package = {
        "decision_run_id": "resolver-api", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": [
            {"ingredient_id": "banana-resolver", "order_quantity": 3, "unit": "kg"},
            {"ingredient_id": "mango-resolver", "order_quantity": 2, "unit": "kg"},
        ]},
        "ingredient_demand": [
            {"ingredient_id": "banana-resolver", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3},
            {"ingredient_id": "mango-resolver", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3},
        ],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []}, "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add_all([
            IngredientModel(ingredient_id="banana-resolver", store_id="STORE_001", ingredient="Chuối", normalized_name="chuối", base_unit="kg", active=True, source="test"),
            IngredientModel(ingredient_id="mango-resolver", store_id="STORE_001", ingredient="Xoài", normalized_name="xoài", base_unit="kg", active=True, source="test"),
            IngredientModel(ingredient_id="saffron-resolver", store_id="STORE_001", ingredient="Saffron", normalized_name="saffron", base_unit="kg", active=True, source="test"),
        ])
        session.add(IngredientAliasModel(alias_id="resolver-banana-en", store_id="STORE_001", ingredient_id="banana-resolver", alias="banana", normalized_alias="banana"))
        session.add(_run("resolver-api", package))
        session.commit()

    alias = client.post("/api/v1/decision-runs/resolver-api/explanation", json={"language": "en", "question": "Why are we buying banana?"})
    assert alias.status_code == 200
    assert alias.json()["entities"]["ingredient_ids"] == ["banana-resolver"]
    generic = client.post("/api/v1/decision-runs/resolver-api/explanation", json={"language": "en", "question": "Why this plan?"})
    assert generic.status_code == 200
    out_of_run = client.post("/api/v1/decision-runs/resolver-api/explanation", json={"question": "Tại sao mua saffron?"})
    assert out_of_run.status_code == 422
    assert out_of_run.json()["code"] == "INGREDIENT_RESOLUTION_NOT_FOUND"
    mismatch = client.post("/api/v1/decision-runs/resolver-api/explanation", json={"ingredient_id": "banana-resolver", "question": "Tại sao mua Xoài?"})
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "INGREDIENT_QUESTION_MISMATCH"
    ambiguous = client.post("/api/v1/decision-runs/resolver-api/explanation", json={"ingredient_id": "banana-resolver", "question": "So sánh Chuối với Xoài"})
    assert ambiguous.status_code == 200


def test_query_interpretation_rejects_out_of_domain_and_gibberish_without_replacing_grounded_quantity(client):
    package = {
        "decision_run_id": "query-interpretation", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": [
            {"ingredient_id": "banana-query", "order_quantity": 3, "unit": "kg"},
            {"ingredient_id": "milk-query", "order_quantity": 50, "unit": "lít"},
        ]},
        "ingredient_demand": [
            {"ingredient_id": "banana-query", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3},
            {"ingredient_id": "milk-query", "target_date": "2026-08-21", "unit": "lít", "p25": 10, "p50": 12, "p75": 14},
        ],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []}, "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add_all([
            IngredientModel(ingredient_id="banana-query", store_id="STORE_001", ingredient="Chuối", normalized_name="chuối", base_unit="kg", active=True, source="test"),
            IngredientModel(ingredient_id="milk-query", store_id="STORE_001", ingredient="Sữa", normalized_name="sữa", base_unit="lít", active=True, source="test"),
            _run("query-interpretation", package),
        ])
        session.commit()

    for question in ("Tôi muốn ăn chuối?", "rekngkjerwgn"):
        response = client.post("/api/v1/decision-runs/query-interpretation/explanation", json={"question": question})
        assert response.status_code == 422
        assert response.json()["code"] == "EXPLANATION_QUERY_UNSUPPORTED"

    false_premise = client.post(
        "/api/v1/decision-runs/query-interpretation/explanation",
        json={"language": "vi", "question": "Tại sao lại phải nhập 70 lít sữa?"},
    )
    assert false_premise.status_code == 200
    assert "70" in false_premise.json()["answer"]
    assert "50" in false_premise.json()["answer"]
    assert all("70" not in str(claim["value"]) for claim in false_premise.json()["claims"])

    matching_premise = client.post(
        "/api/v1/decision-runs/query-interpretation/explanation",
        json={"language": "vi", "question": "Tại sao lại phải nhập 50 lít sữa?"},
    )
    assert matching_premise.status_code == 200
    assert "50" in matching_premise.json()["answer"]


def test_qwen_cannot_promote_a_question_number_to_an_authorized_claim():
    def invented_quantity(payload):
        order = next(item for item in payload["evidence"] if item["type"] == "PROCUREMENT_QUANTITY")
        return {
            "answer": "The plan recommends ordering 70 kg of Banana.",
            "claims": [{"type": "PROCUREMENT_QUANTITY", "text": "The plan recommends ordering 70 kg of Banana.", "evidence_ids": [order["evidence_id"]]}],
            "used_evidence_ids": [order["evidence_id"]],
        }

    response, _ = _explain(invented_quantity, question="Why order 70 kg of Banana?")
    assert response.provider == "deterministic_fallback"
    assert "records 30 kg" in response.answer
    assert all("70" not in str(claim.value) for claim in response.claims)
