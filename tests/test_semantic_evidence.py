from datetime import date, datetime, timezone

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    IngredientDemandBrief,
    ProcurementRowBrief,
    RecommendationBrief,
    RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider, aggregate_evidence
from app.decision_intelligence.semantic_evidence import (
    DecisionSemanticEvidenceBuilder,
    SemanticFactClassification,
)


def _brief(*, demands=None, quantity=30.0, unit="kg"):
    return DecisionBriefFacts(
        decision_run_id="semantic-run",
        store_id="store-1",
        status="completed",
        forecast=ForecastBrief(horizon_days=3, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[
            ProcurementRowBrief(
                ingredient_id="banana",
                ingredient_name="Banana",
                supplier_id="supplier-1",
                quantity=quantity,
                unit=unit,
                pack_count=6,
                pack_size=5,
                reason_codes=["PACK_SIZE_ROUNDING"],
            )
        ],
        ingredient_demand=demands
        or [
            IngredientDemandBrief(
                ingredient_id="banana", ingredient_name="Banana", unit="kg",
                target_date=date(2026, 8, 21), p25=8.0, p50=9.5, p75=10.0,
            ),
            IngredientDemandBrief(
                ingredient_id="banana", ingredient_name="Banana", unit="kg",
                target_date=date(2026, 8, 22), p25=9.0, p50=10.0, p75=11.0,
            ),
            IngredientDemandBrief(
                ingredient_id="banana", ingredient_name="Banana", unit="kg",
                target_date=date(2026, 8, 23), p25=8.5, p50=9.45, p75=10.5,
            ),
        ],
        risk=RiskBrief(),
        critic=CriticBrief(),
        generated_at=datetime.now(timezone.utc),
    )


def _facts_by_type(facts, fact_type):
    return [fact for fact in facts if fact.fact_type == fact_type]


def test_demand_horizon_summary_is_sorted_and_matches_daily_quantile_aggregation():
    demands = [
        IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 24), p25=4, p50=8, p75=10),
        IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 21), p25=2, p50=5, p75=7),
        IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 23), p25=3, p50=9, p75=11),
    ]

    summary = _facts_by_type(DecisionSemanticEvidenceBuilder().build(_brief(demands=demands)), "DEMAND_HORIZON_SUMMARY")[0]

    assert summary.classification is SemanticFactClassification.DERIVED
    assert summary.values["period_start"] == "2026-08-21"
    assert summary.values["period_end"] == "2026-08-24"
    assert summary.values["p25_total"] == 9.0
    assert summary.values["p50_total"] == 22.0
    assert summary.values["p75_total"] == 28.0
    assert summary.values["daily_p50_min"] == 5.0
    assert summary.values["daily_p50_max"] == 9.0
    assert summary.values["peak_date"] == "2026-08-23"
    assert summary.values["peak_p50"] == 9.0
    assert summary.values["aggregation_method"] == "sum_daily_quantiles"


def test_demand_order_alignment_is_numeric_only_not_pack_rounding_causality():
    facts = DecisionSemanticEvidenceBuilder().build(_brief())

    alignment = _facts_by_type(facts, "DEMAND_ORDER_ALIGNMENT")[0]
    assert alignment.classification is SemanticFactClassification.DERIVED
    assert alignment.values["p50_total"] == 28.95
    assert alignment.values["order_quantity_total"] == 30.0
    assert round(alignment.values["absolute_gap"], 8) == 1.05
    assert round(alignment.values["relative_gap"], 8) == round(1.05 / 28.95, 8)
    assert not _facts_by_type(facts, "PACK_SIZE_BINDING")


def test_fact_ids_are_stable_for_the_same_persisted_decision():
    builder = DecisionSemanticEvidenceBuilder()
    first = builder.build(_brief())
    second = builder.build(_brief())
    assert [fact.fact_id for fact in first] == [fact.fact_id for fact in second]


def test_zero_demand_does_not_divide_by_zero():
    demands = [
        IngredientDemandBrief(ingredient_id="banana", ingredient_name="Banana", unit="kg", target_date=date(2026, 8, 21), p25=0, p50=0, p75=0),
    ]
    alignment = _facts_by_type(DecisionSemanticEvidenceBuilder().build(_brief(demands=demands)), "DEMAND_ORDER_ALIGNMENT")[0]
    assert alignment.values["absolute_gap"] == 30.0
    assert alignment.values["relative_gap"] is None


def test_unit_mismatch_skips_alignment_instead_of_comparing_values():
    facts = DecisionSemanticEvidenceBuilder().build(_brief(unit="g"))
    assert not _facts_by_type(facts, "DEMAND_ORDER_ALIGNMENT")


def test_weak_raw_pack_size_reason_is_not_trusted_or_sent_as_procurement_reason():
    brief = _brief()
    package = {
        "reason_codes": [{
            "code": "PACK_SIZE_ROUNDING",
            "entity_id": "banana",
            "evidence": {"pack_size": 5, "final_order_quantity": 30},
        }],
    }
    facts = DecisionSemanticEvidenceBuilder().build(brief, package)
    assert not [fact for fact in facts if fact.classification is SemanticFactClassification.CAUSAL]
    assert not _facts_by_type(facts, "PACK_SIZE_BINDING")

    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    structured = aggregate_evidence(brief, evidence.items, semantic_facts=facts)
    assert not [record for record in structured if record["type"] == "PROCUREMENT_REASON"]


def test_legacy_moq_data_cannot_attach_to_this_decision_run():
    facts = DecisionSemanticEvidenceBuilder().build(_brief(), {
        "legacy_procurement_plan": {"ingredient_id": "banana", "moq": 50},
    })
    assert not _facts_by_type(facts, "MOQ_BINDING")


def test_baseline_preserves_existing_inbound_semantics_without_allocating_across_ingredients():
    package = {
        "inventory_risk": {
            "results": [{
                "scenario_id": "baseline",
                "simulation_start_date": "2026-08-21",
                "simulation_end_date": "2026-08-23",
                "summary": {"by_key": [{
                    "ingredient_id": "banana", "unit": "kg", "total_demand": 28.95,
                    "fulfilled_quantity": 20.0, "shortage_quantity": 8.95,
                    "ending_inventory": 0.0, "fill_rate": 0.69,
                    "projected_stockout_date": "2026-08-23", "stockout_event_count": 1,
                    "explicit_waste_quantity": 0.0,
                }]},
            }],
        },
    }
    facts = _facts_by_type(DecisionSemanticEvidenceBuilder().build(_brief(), package), "NO_PLANNED_PURCHASE_BASELINE")

    assert {fact.scope.value for fact in facts} == {"RUN", "INGREDIENT"}
    ingredient = next(fact for fact in facts if fact.scope.value == "INGREDIENT")
    assert ingredient.values["planned_purchases_from_decision_run_excluded"] is True
    assert ingredient.values["existing_inbound_retained"] is True
    assert ingredient.values["shortage_quantity"] == 8.95


def test_risk_signals_and_limitations_remain_distinct():
    package = {
        "warnings": ["RISK_METRIC_NOT_AVAILABLE"],
        "critic": {"warnings": ["STRESS_SHORTAGE_OBSERVED"]},
        "stress_tests": {"results": [{
            "scenario_id": "demand-plus-20",
            "summary": {"by_key": [{
                "ingredient_id": "banana", "unit": "kg", "shortage_quantity": 2.0,
                "capacity_violation_quantity": 0.0,
            }]},
        }]},
    }
    facts = DecisionSemanticEvidenceBuilder().build(_brief(), package)

    limitation = _facts_by_type(facts, "RISK_METRIC_NOT_AVAILABLE")[0]
    risk_signal = _facts_by_type(facts, "STRESS_SHORTAGE_OBSERVED")[0]
    assert limitation.classification is SemanticFactClassification.LIMITATION
    assert risk_signal.classification is SemanticFactClassification.RISK_SIGNAL
    stress = [fact for fact in _facts_by_type(facts, "STRESS_SHORTAGE_OBSERVED") if fact.scope.value == "INGREDIENT"]
    assert stress and stress[0].values["shortage_quantity"] == 2.0


def test_repeated_limitation_code_keeps_distinct_provenance_fact_ids():
    facts = DecisionSemanticEvidenceBuilder().build(_brief(), {
        "warnings": ["CAPACITY_NOT_EVALUATED"],
        "critic": {"warnings": ["CAPACITY_NOT_EVALUATED"]},
    })
    limitations = _facts_by_type(facts, "CAPACITY_NOT_EVALUATED")
    assert len(limitations) == 2
    assert len({fact.fact_id for fact in limitations}) == 2


def test_grounding_allows_alignment_observation_but_rejects_unsupported_cause():
    provider = DecisionNarrativeProvider(None, None)
    derived = [{"type": "DEMAND_ORDER_ALIGNMENT", "classification": "DERIVED"}]

    provider._validate_causal_language(
        "Nhu cau P50 la 28.95 kg va luong dat la 30 kg.", derived,
    )
    try:
        provider._validate_causal_language("30 kg do pack size.", derived)
    except ValueError as exc:
        assert str(exc) == "unsupported_causal_claim"
    else:
        raise AssertionError("unsupported causal wording was accepted")
