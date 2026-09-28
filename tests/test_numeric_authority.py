from decimal import Decimal

import pytest

from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.numeric_authority import (
    NumericKind,
    build_numeric_authority,
    equivalent_or_rounded,
    iter_numeric_mentions,
)


def _mention(text):
    return next(iter_numeric_mentions(text))


def test_registry_characterizes_budget_strategy_ingredient_and_what_if_provenance():
    evidence = [
        {"evidence_id": "budget", "type": "BUDGET_STATUS", "budget_limit": 10_000_000,
         "planned_spend": 10_400_000, "over_by": 400_000, "remaining_budget": 0,
         "budget_utilization_pct": 104, "currency": "VND"},
        {"evidence_id": "balanced", "type": "STRATEGY_CANDIDATE_METRICS", "strategy": "balanced",
         "purchase_cost": 10_400_000, "expected_fill_rate": .8237, "stockout_probability": .02},
        {"evidence_id": "ingredient", "type": "DEMAND_ORDER_ALIGNMENT", "ingredient_id": "milk",
         "unit": "kg", "absolute_gap": 1.05, "order_quantity_total": 30},
        {"evidence_id": "hypothesis", "type": "WHAT_IF", "scenario_id": "hypothetical",
         "budget_limit": 10_500_000},
    ]
    facts = build_numeric_authority(evidence)
    by_key = {(fact.evidence_id, fact.semantic_key): fact for fact in facts}
    assert by_key[("budget", "budget_limit")].kind is NumericKind.MONEY
    assert by_key[("budget", "budget_utilization_pct")].value == Decimal("104")
    assert by_key[("balanced", "expected_fill_rate")].value == Decimal("82.37")
    assert by_key[("ingredient", "absolute_gap")].kind is NumericKind.QUANTITY
    assert by_key[("ingredient", "absolute_gap")].unit == "kg"
    assert by_key[("hypothesis", "budget_limit")].scenario_id == "hypothetical"


@pytest.mark.parametrize(("engine", "spoken"), [
    (7_390_000, "7.390.000 đồng"),
    (7_390_000, "7,39 triệu"),
])
def test_equivalent_money_formats_are_authorized(engine, spoken):
    fact = build_numeric_authority([{"evidence_id": "x", "purchase_cost": engine}])[0]
    assert equivalent_or_rounded(_mention(spoken), fact, approximate=False)


def test_rounding_type_and_unit_policy_is_centralized():
    money = build_numeric_authority([{"evidence_id": "m", "purchase_cost": 7_390_000}])[0]
    percent = build_numeric_authority([{"evidence_id": "p", "fill_rate": .8237}])[0]
    quantity = build_numeric_authority([{"evidence_id": "q", "unit": "kg", "quantity": 1.05}])[0]
    assert equivalent_or_rounded(_mention("7,4 triệu"), money, approximate=True)
    assert equivalent_or_rounded(_mention("82,4%"), percent, approximate=True)
    assert equivalent_or_rounded(_mention("1,05 kg"), quantity, approximate=False)
    assert not equivalent_or_rounded(_mention("1,05 triệu"), quantity, approximate=False)
    assert not equivalent_or_rounded(_mention("2 ngày"), money, approximate=False)


def test_unique_repair_never_chooses_the_nearest_money_fact():
    provider = DecisionNarrativeProvider(None, None)
    ambiguous = [
        {"evidence_id": "a", "purchase_cost": 9_800_000},
        {"evidence_id": "b", "purchase_cost": 10_200_000},
        {"evidence_id": "c", "purchase_cost": 10_400_000},
    ]
    assert provider._repair_numeric_text("The cost is about 10 million.", ambiguous) == ("The cost is about 10 million.", 0)
    repaired, count = provider._repair_numeric_text("Balanced costs 10,5 triệu.", [{"evidence_id": "balanced", "purchase_cost": 10_400_000}])
    assert count == 1 and "10,4 triệu đồng" in repaired


def test_sentence_removal_salvages_one_local_error_but_not_multiple_material_errors():
    provider = DecisionNarrativeProvider(None, None)
    evidence = [{"evidence_id": "x", "purchase_cost": 10_400_000}]
    assert provider._remove_invalid_numeric_sentences(
        "Balanced is feasible. It costs 12 triệu. Fill rate is retained.", evidence,
    ) == "Balanced is feasible. Fill rate is retained."
    assert provider._remove_invalid_numeric_sentences("It costs 12 triệu. It costs 13 triệu.", evidence) == ""
