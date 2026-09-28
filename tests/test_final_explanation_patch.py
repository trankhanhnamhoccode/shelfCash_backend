"""Final risk and structured-ambiguity regressions before chat exit review."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import PlanningError
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.models.business import IngredientModel
from app.schemas.decision import ExplanationRequest
from app.services.decision.explain_decision import ExplainDecision
from tests.test_chat9_conversational_resolution import Gateway


def _risk_brief():
    return DecisionBriefFacts(
        decision_run_id="final-risk", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 28)),
        recommendation=RecommendationBrief(available=True, strategy="lean"),
        procurement_rows=[], ingredient_demand=[],
        risk=RiskBrief(shortage_quantity=0, expected_fill_rate=1),
        critic=CriticBrief(warnings=["STRESS_SHORTAGE_OBSERVED"]),
        generated_at=datetime.now(timezone.utc),
    )


def _risk_facts(brief):
    package = {
        "stress_tests": {"results": [{"scenario_id": "high_demand", "summary": {"by_key": [{
            "ingredient_id": "orange", "unit": "kg", "shortage_quantity": 3,
            "capacity_violation_quantity": 0,
        }]}}]},
        "critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"], "findings": []},
    }
    return DecisionSemanticEvidenceBuilder().build(brief, package)


def test_risk_retrieval_leads_with_direct_scenario_evidence():
    brief = _risk_brief()
    gateway = Gateway("Trong kịch bản stress, mô phỏng ghi nhận nguy cơ thiếu hàng.")
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=_risk_facts(brief),
        question_scope="general_decision",
    )
    assert gateway.payload["business_brief"][0]["type"] == "STRESS_SHORTAGE_OBSERVED"
    assert "stress" in gateway.payload["business_brief"][0]["text"].lower()
    assert response.provider == "openrouter_qwen", response.llm_diagnostics
    assert response.claims and response.citations


def test_explicit_main_risk_classification_is_not_treated_as_causality():
    brief = _risk_brief()
    response = DecisionNarrativeProvider(Gateway("Rủi ro chính là nguy cơ thiếu hàng trong kịch bản stress."), None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=_risk_facts(brief), question_scope="general_decision",
    )
    assert response.provider == "openrouter_qwen", response.llm_diagnostics


@pytest.mark.parametrize("answer", [
    "Kế hoạch này sẽ thiếu 3 kg.",
    "Đã xảy ra hết hàng trong kịch bản stress.",
    "Xác suất thiếu hàng của kế hoạch là 20%.",
])
def test_stress_signal_cannot_be_promoted_to_selected_actual_or_probability(answer):
    brief = _risk_brief()
    response = DecisionNarrativeProvider(Gateway(answer), None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=_risk_facts(brief), question_scope="general_decision",
    )
    assert response.provider == "deterministic_fallback"
    assert "kịch bản stress" in response.answer
    assert response.claims and response.citations


def test_risk_causal_assertion_remains_rejected_with_safe_risk_fallback():
    brief = _risk_brief()
    gateway = Gateway("Rủi ro thiếu hàng xảy ra vì nhu cầu cao.")
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=_risk_facts(brief),
        question_scope="general_decision",
    )
    assert response.provider == "deterministic_fallback"
    assert response.llm_diagnostics["error_message"] == "unsupported_causal_claim"
    assert "kịch bản stress" in response.answer
    assert "Persisted critic" not in response.answer
    assert response.claims and response.citations
    assert {claim.evidence_ids[0] for claim in response.claims} <= {citation.evidence_id for citation in response.citations}


def test_risk_without_support_has_explicit_limitation_not_generic_recommendation():
    brief = _risk_brief().model_copy(update={"risk": RiskBrief(), "critic": CriticBrief()})
    response = DecisionNarrativeProvider(None, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=[], question_scope="general_decision",
    )
    assert "chưa đủ" in response.answer
    assert "Persisted" not in response.answer
    assert not response.claims and not response.citations


def test_selected_plan_risk_is_not_replaced_by_stress_scenario():
    brief = _risk_brief().model_copy(update={
        "risk": RiskBrief(shortage_quantity=2, expected_fill_rate=0.9),
    })
    facts = _risk_facts(brief)
    gateway = Gateway("Kế hoạch được chọn có nguy cơ thiếu hàng theo mô phỏng, với lượng thiếu dự kiến 2.")
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="general_decision",
    )
    assert gateway.payload["business_brief"][0]["type"] == "SELECTED_PLAN_RISK_METRICS"
    assert response.provider == "openrouter_qwen", response.llm_diagnostics


def test_stress_sentence_cites_stress_fact_even_when_selected_risk_is_also_present():
    brief = _risk_brief().model_copy(update={"risk": RiskBrief(shortage_quantity=2)})
    response = DecisionNarrativeProvider(Gateway("Trong kịch bản stress, mô phỏng ghi nhận nguy cơ thiếu hàng 3 kg."), None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=_risk_facts(brief), question_scope="general_decision",
    )
    assert response.provider == "openrouter_qwen", response.llm_diagnostics
    assert response.citations
    assert all("STRESS_SHORTAGE_OBSERVED" in citation.label for citation in response.citations)


def test_warning_code_alone_cannot_supply_shortage_quantity():
    brief = _risk_brief().model_copy(update={"risk": RiskBrief()})
    facts = DecisionSemanticEvidenceBuilder().build(brief, {"critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"]}})
    gateway = Gateway("Trong kịch bản stress, mô phỏng thiếu 3 kg.")
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="general_decision",
    )
    assert response.provider == "deterministic_fallback"
    assert "3 kg" not in response.answer
    assert "không cung cấp lượng thiếu" in response.answer
    assert response.claims and response.citations


def test_stress_capacity_quantity_is_kept_in_stress_scope():
    brief = _risk_brief().model_copy(update={"risk": RiskBrief(), "critic": CriticBrief()})
    facts = DecisionSemanticEvidenceBuilder().build(brief, {"stress_tests": {"results": [{
        "scenario_id": "capacity", "summary": {"by_key": [{
            "ingredient_id": "orange", "unit": "kg", "shortage_quantity": 0,
            "capacity_violation_quantity": 4,
        }]},
    }]}})
    response = DecisionNarrativeProvider(None, None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="general_decision",
    )
    assert "kịch bản stress" in response.answer and "4 kg" in response.answer
    assert "thiếu hàng" not in response.answer
    assert response.claims and response.citations


def test_capacity_signal_does_not_authorize_shortage_claim():
    brief = _risk_brief().model_copy(update={"risk": RiskBrief(), "critic": CriticBrief()})
    facts = DecisionSemanticEvidenceBuilder().build(brief, {"stress_tests": {"results": [{
        "scenario_id": "capacity", "summary": {"by_key": [{
            "ingredient_id": "orange", "unit": "kg", "shortage_quantity": 0,
            "capacity_violation_quantity": 4,
        }]},
    }]}})
    response = DecisionNarrativeProvider(Gateway("Rủi ro chính là thiếu hàng trong kịch bản stress."), None).explain(
        brief, question="Rủi ro chính của kế hoạch này là gì?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="general_decision",
    )
    assert response.provider == "deterministic_fallback"
    assert "sức chứa" in response.answer


def _milk_service(client):
    names = [("fresh", "Sữa tươi"), ("condensed", "Sữa đặc")]
    brief = DecisionBriefFacts(
        decision_run_id="final-milk", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 28)),
        recommendation=RecommendationBrief(available=True, strategy="lean"),
        procurement_rows=[ProcurementRowBrief(ingredient_id=key, ingredient_name=name, quantity=5, unit="kg") for key, name in names],
        ingredient_demand=[IngredientDemandBrief(ingredient_id=key, ingredient_name=name, unit="kg", target_date=date(2026, 9, 29), p25=5, p50=5, p75=5) for key, name in names],
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )
    with client.app.state.session_factory() as session:
        session.add_all(IngredientModel(ingredient_id=key, store_id="STORE_001", ingredient=name,
            normalized_name=name.casefold(), base_unit="kg", active=True, source="test") for key, name in names)
        session.commit()
    service = ExplainDecision(
        SimpleNamespace(build=lambda _run: brief),
        SimpleNamespace(read=lambda _run: {"ingredient_demand": [{"ingredient_id": key} for key, _ in names], "recommended_plan": {"items": []}}),
        None, None, session_factory=client.app.state.session_factory,
    )
    return service


def test_ambiguous_milk_uses_existing_structured_422_api_contract(client, monkeypatch):
    service = _milk_service(client)
    facade = client.app.state.decision_planning_service
    monkeypatch.setattr(facade, "explain_decision", lambda run_id, body: service.explain(run_id, body))
    response = client.post("/api/v1/decision-runs/final-milk/explanation", json={"question": "Tại sao phải nhập Sữa?"})
    assert response.status_code == 422
    error = response.json()
    assert error["code"] == "INGREDIENT_RESOLUTION_AMBIGUOUS"
    assert {item["ingredient_name"] for item in error["details"]["candidates"]} == {"Sữa tươi", "Sữa đặc"}


@pytest.mark.parametrize(("name", "ingredient_id"), [("Sữa tươi", "fresh"), ("Sữa đặc", "condensed")])
def test_bare_candidate_continues_original_why_question_from_bounded_history(client, name, ingredient_id):
    service = _milk_service(client)
    history = [
        {"role": "user", "content": "Tại sao phải nhập Sữa?"},
        {"role": "assistant", "content": "Bạn muốn hỏi Sữa tươi hay Sữa đặc?"},
    ]
    result = service.explain("final-milk", ExplanationRequest(question=name, history=history))
    assert result["entities"]["ingredient_ids"] == [ingredient_id]
    assert result["intent"] in {"WHY_PROCUREMENT", "EXPLAIN_INGREDIENT_PROCUREMENT"}


def test_ambiguous_milk_never_guesses_candidate(client):
    service = _milk_service(client)
    with pytest.raises(PlanningError) as captured:
        service.explain("final-milk", ExplanationRequest(question="Tại sao phải nhập Sữa?"))
    assert captured.value.code == "INGREDIENT_RESOLUTION_AMBIGUOUS"
