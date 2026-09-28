"""Deterministic reproductions of CHAT-8 live narrative failures."""
from datetime import date, datetime, timezone

import pytest

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.decision_intelligence.semantic_state import validate_semantic_state
from tests.test_budget_explanation import _brief as budget_brief, _package as budget_package
from tests.test_ingredient_explanation import _brief as baseline_brief, _package as baseline_package


@pytest.mark.parametrize(("availability", "exceeds", "answer", "valid"), [
    ("available", False, "Không có dữ liệu ngân sách.", False),
    ("not_available", None, "Hiện chưa có dữ liệu ngân sách để kết luận.", True),
    ("available", False, "Kế hoạch không vượt ngân sách.", True),
    ("available", False, "Kế hoạch đang vượt ngân sách.", False),
    ("available", True, "Kế hoạch vượt ngân sách.", True),
    ("available", True, "Kế hoạch vẫn nằm trong ngân sách.", False),
])
def test_budget_state_assertions(availability, exceeds, answer, valid):
    fact = {"type": "BUDGET_STATUS", "availability": availability, "exceeds_budget": exceeds}
    if valid:
        validate_semantic_state(answer, [fact])
    else:
        with pytest.raises(ValueError):
            validate_semantic_state(answer, [fact])


class Gateway:
    available = True

    def __init__(self, answer):
        self.answer = answer
        self.payload = None

    async def generate_json(self, _system, payload, **_kwargs):
        self.payload = payload
        return {"answer": self.answer}


@pytest.mark.parametrize("answer", [
    "Không có dữ liệu ngân sách hiện tại để xác định kế hoạch có vượt ngân sách hay không.",
    "Kế hoạch đang vượt ngân sách.",
])
def test_live_budget_contradiction_never_returns_qwen_grounded(answer):
    brief = budget_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, budget_package(10_000_000, 7_668_000))
    gateway = Gateway(answer)
    result = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Kế hoạch có vượt ngân sách không?", language="vi",
        detail_level="simple", semantic_facts=facts, question_scope="budget",
    )
    assert result.provider == "deterministic_fallback"
    assert result.llm_diagnostics["failure_stage"] == "GROUNDING"
    assert gateway.payload["business_brief"][0]["text"].startswith("Budget data available")
    assert "Không có dữ liệu" not in result.answer


def ingredient_brief(order=40, demand=58.8808125, unit="kg"):
    return DecisionBriefFacts(
        decision_run_id="live-ingredient", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 26)),
        recommendation=RecommendationBrief(available=True, strategy="lean"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="orange", ingredient_name="Cam", quantity=order, unit=unit)],
        ingredient_demand=[IngredientDemandBrief(ingredient_id="orange", ingredient_name="Cam", unit=unit, target_date=date(2026, 9, 27), p25=demand, p50=demand, p75=demand)],
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(("order", "wording"), [(40, "thấp hơn"), (70, "cao hơn"), (58.8808125, "bằng")])
def test_alignment_direction_and_authoritative_unit(order, wording):
    brief = ingredient_brief(order)
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    result = ShelfCashDecisionIntelligenceAdapter().explain_ingredient(
        brief, ingredient_id="orange", language="vi", detail_level="simple", semantic_facts=facts,
    )
    assert wording in result.answer
    assert "40 quả" not in result.answer
    assert any(claim.unit == "kg" and claim.value == order for claim in result.claims if claim.type == "PROCUREMENT_QUANTITY")
    public = result.model_dump(mode="json")
    assert "40 quả" not in public["answer"]
    assert "40 quả" not in public["summary"]


def test_count_unit_is_preserved():
    brief = ingredient_brief(12, 15, "quả")
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    result = ShelfCashDecisionIntelligenceAdapter().explain_ingredient(
        brief, ingredient_id="orange", language="vi", detail_level="simple", semantic_facts=facts,
    )
    assert "12 quả Cam" in result.answer


def test_ingredient_invented_causal_shortage_falls_back():
    brief = ingredient_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    gateway = Gateway("Cần nhập 40 kg Cam vì nhu cầu dự báo p50 là 58,88 kg, trong khi số lượng đặt là 40 kg, tạo ra thiếu hụt 18,88 kg.")
    result = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Tại sao cần nhập Cam?", ingredient_id="orange", language="vi",
        detail_level="simple", semantic_facts=facts,
    )
    assert result.provider == "deterministic_fallback"
    assert "thiếu hụt 18,88" not in result.answer
    cards = gateway.payload["business_brief"]
    assert "exact cause is unavailable" in gateway.payload["task_hint"]
    assert any(card["type"] == "DEMAND_ORDER_ALIGNMENT" and "below" in card["text"] and "18,88 kg" in card["text"] for card in cards)
    assert any(card["type"] == "DEMAND_HORIZON_SUMMARY" and "58,88 kg" in card["text"] for card in cards)
    assert any(card["type"] == "PROCUREMENT_QUANTITY" and "buying 40 kg Cam" in card["text"] and "proposed purchase" in card["text"] for card in cards)


def test_why_brief_includes_authoritative_no_purchase_baseline():
    brief = baseline_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, baseline_package())
    gateway = Gateway("The current plan proposes ordering 30 kg Banana.")
    DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Why order Banana?", ingredient_id="banana", language="en",
        detail_level="simple", semantic_facts=facts,
    )
    cards = gateway.payload["business_brief"]
    baseline = next(card for card in cards if card["type"] == "NO_PLANNED_PURCHASE_BASELINE")
    assert "8,95" in baseline["text"]
    assert "existing inbound retained" in baseline["text"]


def test_alignment_magnitude_cannot_masquerade_as_baseline_shortage():
    brief = baseline_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, baseline_package())
    result = DecisionNarrativeProvider(Gateway("Banana has a shortage of 1.05 kg."), None).explain(
        brief, question="Why order Banana?", ingredient_id="banana", language="en",
        detail_level="simple", semantic_facts=facts,
    )
    assert result.provider == "deterministic_fallback"
    assert "shortage of 1.05" not in result.answer


def test_provider_token_limit_falls_back_without_garbage():
    class TokenLimitGateway:
        available = True

        async def generate_json(self, *_args, **_kwargs):
            error = ValueError("token limit")
            error.details = {"failure_stage": "TOKEN_LIMIT", "finish_reason": "length", "completion_tokens": 1200}
            raise error

    brief = budget_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, budget_package(10_000_000, 7_668_000))
    result = DecisionNarrativeProvider(TokenLimitGateway(), None).explain(
        brief, question="Có lố budget không?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="budget",
    )
    assert result.provider == "deterministic_fallback"
    assert result.llm_diagnostics["failure_stage"] == "TOKEN_LIMIT"
    assert "```" not in result.answer


def test_well_formed_but_degenerate_provider_answer_is_not_grounded():
    brief = budget_brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, budget_package(10_000_000, 7_668_000))
    result = DecisionNarrativeProvider(Gateway("!``````````````````````"), None).explain(
        brief, question="Có lố budget không?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="budget",
    )
    assert result.provider == "deterministic_fallback"
    assert result.llm_diagnostics["failure_stage"] == "GROUNDING"
    assert "```" not in result.answer
