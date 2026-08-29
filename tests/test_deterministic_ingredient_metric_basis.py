from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.services.business_metrics_service import build_business_metrics
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, RecommendationBrief, RiskBrief,
)


def _summary(*, scenario_id, demand, fulfilled, shortage, fill, stockout=None):
    item = SimpleNamespace(
        store_id="store", ingredient_id="matcha", unit="kg", total_demand=demand,
        fulfilled_quantity=fulfilled, shortage_quantity=shortage, fill_rate=fill,
        expired_quantity=0, explicit_waste_quantity=0, ending_inventory=0,
        days_of_supply=None, projected_stockout_date=stockout, stockout_event_count=int(shortage > 0),
    )
    return SimpleNamespace(scenario_id=scenario_id, summary=SimpleNamespace(by_key=[item]), daily_ledgers=[], provenance={})


def _metrics(results):
    return build_business_metrics(
        purchase_cost=0, simulation=SimpleNamespace(results=results), recommended=True,
    )["deterministic"]["ingredient_metrics"][0]


def _assert_identity(row):
    assert row["shortage_quantity"] == pytest.approx(max(row["demand_quantity"] - row["fulfilled_quantity"], 0))
    if row["demand_quantity"]:
        assert row["fill_rate"] == pytest.approx(row["fulfilled_quantity"] / row["demand_quantity"])


def test_flat_metric_is_one_complete_conservative_scenario_not_a_mixed_row():
    low = _summary(scenario_id="LOW_P25", demand=10, fulfilled=10, shortage=0, fill=1.0)
    median = _summary(scenario_id="MEDIAN_P50", demand=12, fulfilled=11, shortage=1, fill=11 / 12,
                      stockout=date(2026, 8, 15))
    high = _summary(scenario_id="HIGH_P75", demand=15, fulfilled=12, shortage=3, fill=.8,
                    stockout=date(2026, 8, 14))
    row = _metrics([low, median, high])

    assert row["basis_scenario_id"] == "HIGH_P75"
    assert (row["demand_quantity"], row["fulfilled_quantity"], row["shortage_quantity"], row["fill_rate"]) == (15, 12, 3, .8)
    _assert_identity(row)
    assert {item["scenario_id"] for item in row["scenario_metrics"]} == {"LOW_P25", "MEDIAN_P50", "HIGH_P75"}
    assert row["worst_case"]["minimum_fill_rate"] == {"value": .8, "scenario_id": "HIGH_P75"}
    assert row["worst_case"]["maximum_shortage_quantity"] == {"value": 3, "scenario_id": "HIGH_P75"}
    assert row["worst_case"]["earliest_stockout"] == {"value": date(2026, 8, 14), "scenario_id": "HIGH_P75"}


def test_selector_is_input_order_independent_and_worst_dimensions_keep_own_provenance():
    low_fill = _summary(scenario_id="A", demand=10, fulfilled=7, shortage=3, fill=.7)
    max_shortage = _summary(scenario_id="B", demand=20, fulfilled=16, shortage=4, fill=.8)
    early_stockout = _summary(scenario_id="C", demand=10, fulfilled=9, shortage=1, fill=.9,
                              stockout=date(2026, 8, 12))
    first, reordered = _metrics([low_fill, max_shortage, early_stockout]), _metrics([early_stockout, low_fill, max_shortage])

    assert first["basis_scenario_id"] == reordered["basis_scenario_id"] == "A"
    assert first["scenario_metrics"] == reordered["scenario_metrics"]
    _assert_identity(first)
    assert first["worst_case"]["minimum_fill_rate"]["scenario_id"] == "A"
    assert first["worst_case"]["maximum_shortage_quantity"]["scenario_id"] == "B"
    assert first["worst_case"]["earliest_stockout"]["scenario_id"] == "C"


def test_zero_demand_reuses_simulator_fill_rate_and_does_not_divide():
    row = _metrics([_summary(scenario_id="ZERO", demand=0, fulfilled=0, shortage=0, fill=1.0)])
    assert row["fill_rate"] == 1.0
    _assert_identity(row)


def test_operational_semantic_evidence_carries_coherent_basis_and_stress_is_separate():
    row = _metrics([_summary(scenario_id="BASE", demand=10, fulfilled=10, shortage=0, fill=1.0)])
    brief = DecisionBriefFacts(
        decision_run_id="run", store_id="store", status="completed",
        forecast=ForecastBrief(horizon_days=1, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )
    package = {
        "business_metrics": {"deterministic": {"ingredient_metrics": [row]}},
        "stress_tests": {"results": [{"scenario_id": "stress", "summary": {"by_key": [{
            "ingredient_id": "matcha", "unit": "kg", "shortage_quantity": 2,
        }]}}]},
        "warnings": [],
    }
    facts = DecisionSemanticEvidenceBuilder().build(brief, package)
    operational = [fact for fact in facts if fact.fact_type == "INGREDIENT_OPERATIONAL_RISK"]
    assert operational == []  # full-service basis is not falsely converted into a shortage fact
    stress = [fact for fact in facts if fact.fact_type == "STRESS_SHORTAGE_OBSERVED"]
    assert len(stress) == 1 and stress[0].entities["ingredient_id"] == "matcha"


def test_operational_semantic_evidence_uses_only_its_declared_basis_scenario():
    low = _summary(scenario_id="LOW", demand=10, fulfilled=10, shortage=0, fill=1.0)
    high = _summary(scenario_id="HIGH", demand=15, fulfilled=12, shortage=3, fill=.8)
    row = _metrics([low, high])
    brief = DecisionBriefFacts(
        decision_run_id="run", store_id="store", status="completed",
        forecast=ForecastBrief(horizon_days=1, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )
    facts = DecisionSemanticEvidenceBuilder().build(
        brief, {"business_metrics": {"deterministic": {"ingredient_metrics": [row]}}, "warnings": []},
    )
    fact = next(item for item in facts if item.fact_type == "INGREDIENT_OPERATIONAL_RISK")
    assert fact.values["basis_scenario_id"] == "HIGH"
    assert (fact.values["demand_quantity"], fact.values["fulfilled_quantity"], fact.values["shortage_quantity"], fact.values["fill_rate"]) == (15, 12, 3, .8)
