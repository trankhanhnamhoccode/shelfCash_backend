"""CHAT-9 reproductions across interpretation, retrieval, grounding and provenance."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.models.business import IngredientModel
from app.schemas.decision import ExplanationRequest
from app.services.decision.explain_decision import ExplainDecision
from app.services.decision.explanation_history import resolve_follow_up
from app.services.decision.explanation_ingredient_resolver import ExplanationIngredientResolver
from app.services.decision.explanation_query_interpretation import QuestionScope, classify_question_scope
from tests.test_budget_explanation import _brief as budget_brief, _package as budget_package
from tests.test_strategy_comparison import _brief as strategy_brief, _package as strategy_package


class Gateway:
    available = True

    def __init__(self, answer):
        self.answer = answer
        self.payload = None

    async def generate_json(self, _system, payload, **_kwargs):
        self.payload = payload
        return {"answer": self.answer}


def _ingredient_brief():
    return DecisionBriefFacts(
        decision_run_id="chat9-orange", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 28)),
        recommendation=RecommendationBrief(available=True, strategy="lean"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="orange", ingredient_name="Cam", quantity=40, unit="kg")],
        ingredient_demand=[IngredientDemandBrief(ingredient_id="orange", ingredient_name="Cam", unit="kg", target_date=date(2026, 9, 29), p25=58.93, p50=58.93, p75=58.93)],
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _baseline_package():
    return {"inventory_risk": {"results": [{"scenario_id": "baseline", "summary": {"by_key": [{
        "ingredient_id": "orange", "unit": "kg", "total_demand": 58.93,
        "fulfilled_quantity": 11.6434334, "shortage_quantity": 47.2865666,
        "ending_inventory": 0, "fill_rate": 0.1976,
    }]}}]}}


@pytest.mark.parametrize("answer", [
    "Không có bằng chứng cho thấy kế hoạch nhập Cam để bù thiếu hụt.",
    "Dữ liệu hiện tại không đủ để xác nhận lý do chính xác.",
    "Chưa thể kết luận rằng nhu cầu cao là nguyên nhân nhập Cam.",
])
def test_negated_or_uncertain_procurement_cause_is_not_positive_assertion(answer):
    DecisionNarrativeProvider._validate_causal_language(answer, [])


@pytest.mark.parametrize("answer", [
    "Kế hoạch nhập Cam vì nhu cầu cao.",
    "Kế hoạch nhập Cam để bù thiếu hụt.",
    "Nhu cầu cao dẫn đến việc nhập Cam.",
])
def test_positive_procurement_cause_still_requires_authority(answer):
    with pytest.raises(ValueError, match="unsupported_causal_claim"):
        DecisionNarrativeProvider._validate_causal_language(answer, [])


def test_denied_cause_does_not_hide_a_separate_positive_cause():
    with pytest.raises(ValueError, match="unsupported_causal_claim"):
        DecisionNarrativeProvider._validate_causal_language(
            "Chưa đủ dữ liệu để xác nhận lý do. Nhưng kế hoạch nhập Cam vì nhu cầu cao.", [],
        )


def test_full_live_negation_answer_is_not_rejected_for_causality():
    brief = _ingredient_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _baseline_package())
    answer = ("Không có lý do chính thức nào được cung cấp cho việc nhập Cam. "
              "Dựa trên dữ liệu hiện có, đây chỉ là một đề xuất mua 40 kg, trong khi nhu cầu dự báo (p50) là 58,93 kg. "
              "Việc mua số lượng này thấp hơn nhu cầu dự báo khoảng 18,93 kg, và không có bằng chứng nào cho thấy đây là để bù đắp thiếu hụt hay phục vụ một lý do cụ thể nào khác.")
    response = DecisionNarrativeProvider(Gateway(answer), None).explain(
        brief, question="Tại sao phải nhập Cam?", ingredient_id="orange",
        language="vi", detail_level="simple", semantic_facts=facts,
    )
    assert response.provider == "openrouter_qwen", response.llm_diagnostics
    assert response.answer == answer


@pytest.mark.parametrize(("answer", "provider"), [
    ("Nếu không nhập Cam, mô phỏng baseline cho thấy thiếu hụt khoảng 47,29 kg.", "openrouter_qwen"),
    ("Nếu không nhập Cam, tình trạng thiếu hụt được mô phỏng là khoảng 42,93 kg.", "deterministic_fallback"),
])
def test_no_purchase_card_and_numeric_authority(answer, provider):
    brief = _ingredient_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _baseline_package())
    gateway = Gateway(answer)
    result = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Nếu không nhập Cam thì sao?", ingredient_id="orange",
        language="vi", detail_level="simple", semantic_facts=facts,
    )
    assert result.provider == provider, result.llm_diagnostics
    assert gateway.payload["business_brief"][0]["type"] == "NO_PLANNED_PURCHASE_BASELINE"
    assert "47,29 kg" in gateway.payload["business_brief"][0]["text"]
    assert "existing inbound retained" in gateway.payload["business_brief"][0]["text"]
    assert "42,93" not in result.answer


def _milk_brief(names):
    rows = [ProcurementRowBrief(ingredient_id=key, ingredient_name=name, quantity=5, unit="kg") for key, name in names]
    demand = [IngredientDemandBrief(ingredient_id=key, ingredient_name=name, unit="kg", target_date=date(2026, 9, 29), p25=5, p50=5, p75=5) for key, name in names]
    return DecisionBriefFacts(
        decision_run_id="chat9-milk", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 28)),
        recommendation=RecommendationBrief(available=True, strategy="lean"),
        procurement_rows=rows, ingredient_demand=demand,
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _milk_service(client, names):
    brief = _milk_brief(names)
    package = {"ingredient_demand": [{"ingredient_id": key} for key, _ in names], "recommended_plan": {"items": []}}
    return ExplainDecision(
        SimpleNamespace(build=lambda _run: brief), SimpleNamespace(read=lambda _run: package),
        None, None, session_factory=client.app.state.session_factory,
    )


def test_milk_ambiguity_precedes_generic_plan_and_uses_clarification(client):
    names = [("fresh", "Sữa tươi"), ("condensed", "Sữa đặc")]
    with client.app.state.session_factory() as session:
        session.add_all(IngredientModel(ingredient_id=key, store_id="STORE_001", ingredient=name,
            normalized_name=name.casefold(), base_unit="kg", active=True, source="test") for key, name in names)
        session.commit()
    service = _milk_service(client, names)
    result = service.explain("chat9-milk", ExplanationRequest(question="Sữa trong kế hoạch có cần thiết không?"))
    assert result["intent"] == "CLARIFICATION"
    assert "Sữa tươi" in result["answer"] and "Sữa đặc" in result["answer"]
    assert "không có đề xuất mua sữa" not in result["answer"].casefold()
    assert result["claims"] == [] and result["citations"] == []
    exact = service.explain("chat9-milk", ExplanationRequest(question="Sữa đặc có cần nhập không?"))
    assert exact["entities"]["ingredient_ids"] == ["condensed"]
    unknown = service.explain("chat9-milk", ExplanationRequest(question="Bột cacao có cần nhập không?"))
    assert unknown["intent"] == "CLARIFICATION" or unknown["intent"] == "FALLBACK"
    assert "Sữa" not in unknown["answer"]


def test_unique_milk_family_resolves_and_pending_clarification_uses_client_history(client):
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(ingredient_id="fresh", store_id="STORE_001", ingredient="Sữa tươi",
            normalized_name="sữa tươi", base_unit="kg", active=True, source="test"))
        session.flush()
        resolver = ExplanationIngredientResolver(session)
        result = resolver.resolve(question="Sữa có cần nhập không?", store_id="STORE_001", decision_run_ingredient_ids={"fresh"})
        assert result.ingredient_id == "fresh"
    brief = _milk_brief([("fresh", "Sữa tươi"), ("condensed", "Sữa đặc")])
    history = [SimpleNamespace(role="user", content="Sữa có cần thiết không?"),
               SimpleNamespace(role="assistant", content="Bạn muốn hỏi Sữa tươi hay Sữa đặc?")]
    continuation = resolve_follow_up("Sữa tươi", history, brief)
    assert "Sữa tươi" in continuation.question
    assert classify_question_scope(continuation.question, has_explicit_ingredient_id=False) is not QuestionScope.UNSUPPORTED


def test_unknown_qualified_family_does_not_resolve_to_known_flour(client):
    with client.app.state.session_factory() as session:
        session.add_all(IngredientModel(ingredient_id=key, store_id="STORE_001", ingredient=name,
            normalized_name=name.casefold(), base_unit="kg", active=True, source="test")
            for key, name in [("rice", "Bột gạo"), ("wheat", "Bột mì")])
        session.flush()
        result = ExplanationIngredientResolver(session).resolve(
            question="Bột cacao có cần nhập không?", store_id="STORE_001",
            decision_run_ingredient_ids={"rice", "wheat"},
        )
        assert result.status == "not_found"


@pytest.mark.parametrize("question", ["Tại sao chọn LEAN thay vì BALANCE?", "Tại sao chọn LEAN thay vì BALANCED?"])
def test_strategy_alias_routes_to_pairwise_evidence(question):
    assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY
    brief = strategy_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, strategy_package())
    gateway = Gateway("BALANCED có chi phí mua cao hơn LEAN.")
    DecisionNarrativeProvider(gateway, None).explain(
        brief, question=question, language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="plan_strategy",
    )
    cards = gateway.payload["business_brief"]
    assert any(card["type"] == "STRATEGY_COMPARISON" and "balanced vs lean" in card["text"].lower() for card in cards)
    assert not any(card["type"] == "STRATEGY_COMPARISON" and "protected" in card["text"].lower() for card in cards)


def test_strategy_follow_up_uses_alias_context():
    brief = strategy_brief()
    continuation = resolve_follow_up(
        "Cái nào ít thiếu hàng hơn?",
        [SimpleNamespace(role="user", content="Tại sao chọn LEAN thay vì BALANCE?")], brief,
    )
    assert classify_question_scope(continuation.question, has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY
    assert "balanced" in continuation.question and "lean" in continuation.question


def test_no_purchase_follow_up_resolves_current_ingredient_for_remove_from_order():
    brief = _ingredient_brief()
    history = [SimpleNamespace(role="user", content="Tại sao cần nhập Cam?")]
    continuation = resolve_follow_up("Nếu bỏ nguyên liệu này khỏi đơn thì sao?", history, brief)
    assert "Cam" in continuation.question
    assert classify_question_scope(continuation.question, has_explicit_ingredient_id=False) is QuestionScope.ENTITY_OPERATIONAL


def test_strategy_fallback_sentences_have_matching_public_provenance():
    brief = strategy_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, strategy_package())
    result = DecisionNarrativeProvider(None, None).explain(
        brief, question="Tại sao chọn BALANCED?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="plan_strategy",
    )
    comparison_lines = [line for line in result.why_this_plan if "thấp hơn" in line]
    assert comparison_lines
    for line in comparison_lines:
        claim = next(claim for claim in result.claims if claim.value == line)
        assert claim.type == "STRATEGY_COMPARISON"
        assert set(claim.evidence_ids) <= {citation.evidence_id for citation in result.citations}


def test_accepted_strategy_comparison_has_matching_public_provenance():
    brief = strategy_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, strategy_package())
    result = DecisionNarrativeProvider(Gateway("BALANCED có chi phí mua thấp hơn PROTECTED."), None).explain(
        brief, question="So sánh BALANCED với PROTECTED", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="plan_strategy",
    )
    assert result.provider == "openrouter_qwen", result.llm_diagnostics
    assert result.claims[0].type == "STRATEGY_COMPARISON"
    assert set(result.claims[0].evidence_ids) <= {citation.evidence_id for citation in result.citations}


def test_budget_correct_state_and_amount_remain_accepted():
    brief = budget_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, budget_package(10_000_000, 7_668_000))
    result = DecisionNarrativeProvider(Gateway("Kế hoạch không vượt ngân sách. Còn 2.332.000 VND."), None).explain(
        brief, question="Kế hoạch có vượt ngân sách không?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="budget",
    )
    assert result.provider == "openrouter_qwen", result.llm_diagnostics
