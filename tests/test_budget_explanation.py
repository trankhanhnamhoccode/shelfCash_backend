from datetime import date, datetime, timezone

import pytest

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import CriticBrief, DecisionBriefFacts, ForecastBrief, RecommendationBrief, RiskBrief
from app.decision_intelligence.narrative import DecisionNarrativeProvider, aggregate_evidence
from app.decision_intelligence.narrative_retrieval import retrieve_narrative_evidence
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.services.decision.explanation_query_interpretation import QuestionScope, classify_question_scope


def _brief():
    return DecisionBriefFacts(
        decision_run_id="budget-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 26)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _package(limit, spend):
    return {
        "recommended_strategy": "balanced",
        "strategies": {"balanced": {"strategy": "balanced", "purchase_cost": spend}},
        "technical_metrics": {"scenario_diagnostics": {"budget_snapshot": {
            "configured": limit is not None, "budget_limit": limit, "currency": "VND",
        }}},
    }


@pytest.mark.parametrize("question", (
    "Kế hoạch có vượt quá ngân sách không?",
    "Vượt quá ngân sách hả?",
    "Có lố budget không?",
    "Còn dư bao nhiêu tiền?",
    "Kế hoạch còn bao nhiêu ngân sách?",
))
def test_budget_questions_have_one_deterministic_scope(question):
    assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.BUDGET


def test_existing_plan_and_ingredient_scopes_do_not_regress():
    assert classify_question_scope("Tại sao chọn LEAN thay vì BALANCED?", has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY
    assert classify_question_scope("Tại sao cần nhập sữa tươi?", has_explicit_ingredient_id=False) is QuestionScope.ENTITY_OPERATIONAL


@pytest.mark.parametrize(("limit", "spend", "expected"), (
    (10_000_000, 8_200_000, {"exceeds_budget": False, "over_by": 0.0, "remaining_budget": 1_800_000.0, "budget_utilization_pct": 82.0}),
    (10_000_000, 10_400_000, {"exceeds_budget": True, "over_by": 400_000.0, "remaining_budget": 0.0, "budget_utilization_pct": 104.0}),
    (10_000_000, 10_000_000, {"exceeds_budget": False, "over_by": 0.0, "remaining_budget": 0.0, "budget_utilization_pct": 100.0}),
))
def test_budget_fact_is_deterministically_materialized(limit, spend, expected):
    fact = next(fact for fact in DecisionSemanticEvidenceBuilder().build(_brief(), _package(limit, spend)) if fact.fact_type == "BUDGET_STATUS")
    assert fact.values["availability"] == "available"
    assert fact.values["budget_limit"] == float(limit)
    assert fact.values["planned_spend"] == float(spend)
    assert fact.values["currency"] == "VND"
    for key, value in expected.items():
        assert fact.values[key] == value


@pytest.mark.parametrize(("limit", "spend"), ((None, 8_200_000), (0, 0)))
def test_missing_or_zero_budget_never_invents_budget_state(limit, spend):
    fact = next(fact for fact in DecisionSemanticEvidenceBuilder().build(_brief(), _package(limit, spend)) if fact.fact_type == "BUDGET_STATUS")
    if limit is None:
        assert fact.values["availability"] == "not_available"
    else:
        assert fact.values["availability"] == "available"
        assert fact.values["budget_utilization_pct"] is None
        assert fact.values["exceeds_budget"] is False


def test_budget_retrieval_and_fallback_are_budget_first():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package(10_000_000, 10_400_000))
    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    records = aggregate_evidence(brief, evidence.items, semantic_facts=facts)
    retrieval = retrieve_narrative_evidence(
        brief, records, question="Có lố budget không?", ingredient_id=None,
        detail_level="simple", question_scope=QuestionScope.BUDGET.value,
    )
    assert retrieval.intent == "BUDGET"
    assert [item["type"] for item in retrieval.evidence] == ["BUDGET_STATUS"]

    result = DecisionNarrativeProvider(None, None).explain(
        brief, question="Có lố budget không?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope=QuestionScope.BUDGET.value,
    )
    assert "10,4 triệu đồng" in result.answer
    assert "400 nghìn đồng" in result.answer


class _PayloadGateway:
    available = True

    async def generate_json(self, _system, payload, **_kwargs):
        self.payload = payload
        item = next(item for item in payload["business_brief"] if item["type"] == "BUDGET_STATUS")
        return {"answer": "Kế hoạch vượt ngân sách.", "claims": [{
            "type": "BUDGET_STATUS", "text": "Kế hoạch vượt ngân sách.",
            "evidence_ids": [item["evidence_id"]],
        }], "used_evidence_ids": [item["evidence_id"]]}


def test_budget_provider_payload_has_budget_evidence_as_primary_retrieval():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package(10_000_000, 10_400_000))
    gateway = _PayloadGateway()
    DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Kế hoạch có vượt quá ngân sách không?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope=QuestionScope.BUDGET.value,
    )
    assert [item["type"] for item in gateway.payload["business_brief"]] == ["BUDGET_STATUS"]
    assert gateway.payload["task_hint"]
    assert "communication_plan" not in gateway.payload


def test_provider_repairs_one_budget_amount_from_its_uniquely_cited_fact():
    class Gateway:
        available = True

        async def generate_json(self, _system, payload, **_kwargs):
            budget = next(item for item in payload["business_brief"] if item["type"] == "BUDGET_STATUS")
            text = "Kế hoạch dự kiến chi 10,5 triệu đồng."
            return {"answer": text, "claims": [{
                "type": "BUDGET_STATUS", "text": text, "evidence_ids": [budget["evidence_id"]],
            }], "used_evidence_ids": [budget["evidence_id"]]}

    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package(10_000_000, 10_400_000))
    response = DecisionNarrativeProvider(Gateway(), None).explain(
        brief, question="Kế hoạch chi bao nhiêu?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope=QuestionScope.BUDGET.value,
    )
    assert response.provider == "openrouter_qwen"
    assert "10,4 triệu đồng" in response.answer
    assert "10,5" not in response.answer
    assert all("10,5" not in str(claim.value) for claim in response.claims)


def test_stale_history_number_cannot_override_current_budget_authority():
    class Gateway:
        available = True

        async def generate_json(self, _system, payload, **_kwargs):
            budget = next(item for item in payload["business_brief"] if item["type"] == "BUDGET_STATUS")
            text = "Ngân sách hiện tại là 20 triệu đồng."
            return {"answer": text, "claims": [{
                "type": "BUDGET_STATUS", "text": text, "evidence_ids": [budget["evidence_id"]],
            }], "used_evidence_ids": [budget["evidence_id"]]}

    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package(10_000_000, 10_400_000))
    history = [type("Turn", (), {"role": "assistant", "content": "Ngân sách là 20 triệu."})()]
    response = DecisionNarrativeProvider(Gateway(), None).explain(
        brief, question="Ngân sách hiện tại là bao nhiêu?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope=QuestionScope.BUDGET.value, history=history,
    )
    assert response.provider == "openrouter_qwen"
    assert "10 triệu đồng" in response.answer
    assert "20 triệu" not in response.answer
