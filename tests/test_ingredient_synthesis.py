from datetime import date, datetime, timezone

from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.ingredient_synthesis import IngredientSynthesisProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.decision_intelligence.warning_presentation import present_warnings


def _brief():
    return DecisionBriefFacts(
        decision_run_id="ingredient-presentation", store_id="store", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="watch", ingredient_name="Tra", quantity=3, unit="kg")],
        ingredient_demand=[
            IngredientDemandBrief(ingredient_id="normal", ingredient_name="Sua", unit="lít", target_date=date(2026, 8, 21), p25=1, p50=1.25, p75=2),
            IngredientDemandBrief(ingredient_id="watch", ingredient_name="Tra", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
        ], risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def test_every_ingredient_has_deterministic_synthesis_and_display_values():
    brief = _brief()
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [{"ingredient_id": "watch", "unit": "kg", "shortage_quantity": 0.5, "stockout_event_count": 1}]}}, "warnings": []}
    facts = DecisionSemanticEvidenceBuilder().build(brief, package)
    result = IngredientSynthesisProvider(None, None).synthesize(brief, facts)
    assert [item.ingredient_id for item in result] == ["normal", "watch"]
    assert result[0].importance == "normal"
    assert result[0].source == "rule_based"
    assert "1,25" in result[0].summary
    assert result[1].importance == "watch"
    assert result[1].source == "rule_based"


def test_warning_presentation_is_deterministic_and_hides_unknown_machine_code():
    warnings = {item.code: item for item in present_warnings([
        "CAPACITY_NOT_EVALUATED", "STRESS_SHORTAGE_OBSERVED",
        "UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS", "SOME_NEW_CODE",
    ])}
    assert warnings["CAPACITY_NOT_EVALUATED"].audience == "user"
    assert warnings["CAPACITY_NOT_EVALUATED"].title == "Chưa thể đánh giá đầy đủ sức chứa kho"
    assert warnings["STRESS_SHORTAGE_OBSERVED"].title != "STRESS_SHORTAGE_OBSERVED"
    assert warnings["STRESS_SHORTAGE_OBSERVED"].title == "Có nguy cơ thiếu hàng trong một số tình huống"
    assert warnings["UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS"].audience == "technical"
    assert warnings["SOME_NEW_CODE"].audience == "technical"
    assert warnings["SOME_NEW_CODE"].title != "SOME_NEW_CODE"


class _Gateway:
    available = True

    def __init__(self):
        self.calls = 0

    async def generate_json(self, _prompt, payload, **_kwargs):
        self.calls += 1
        items = []
        for ingredient in payload["ingredients"]:
            risk = next(item for item in ingredient["evidence"] if item["type"] == "INGREDIENT_OPERATIONAL_RISK")
            evidence_id = risk["evidence_id"]
            # The second item is deliberately invalid: it cites another item.
            used = [evidence_id] if ingredient["ingredient_id"] == "critical-a" else ["not-authorized"]
            items.append({
                "ingredient_id": ingredient["ingredient_id"], "headline": "Cần theo dõi", "summary": "Có thể thiếu hàng trong kỳ kế hoạch.",
                "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": "Có thể thiếu hàng trong kỳ kế hoạch.", "evidence_ids": used}],
                "used_evidence_ids": used,
            })
        return {"items": items}


def test_critical_ingredients_use_one_batch_and_invalid_item_alone_falls_back():
    brief = _brief().model_copy(update={
        "ingredient_demand": [
            IngredientDemandBrief(ingredient_id="critical-a", ingredient_name="A", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
            IngredientDemandBrief(ingredient_id="critical-b", ingredient_name="B", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
        ], "procurement_rows": [],
    })
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [
        {"ingredient_id": "critical-a", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
        {"ingredient_id": "critical-b", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
    ]}}, "warnings": []}
    gateway = _Gateway()
    result = IngredientSynthesisProvider(gateway, None).synthesize(brief, DecisionSemanticEvidenceBuilder().build(brief, package))
    assert gateway.calls == 1
    assert {item.ingredient_id: item.source for item in result} == {"critical-a": "llm", "critical-b": "deterministic_fallback"}
