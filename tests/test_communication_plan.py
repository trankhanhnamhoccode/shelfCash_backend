from app.decision_intelligence.communication_plan import (
    narrative_communication_plan,
    summary_communication_plan,
    what_if_communication_plan,
)


def _record(identifier, type_, classification="OBSERVATION"):
    return {"evidence_id": identifier, "type": type_, "classification": classification}


def test_summary_plan_prioritizes_decision_then_risk_then_limitation():
    plan = summary_communication_plan([
        _record("decision", "PLAN_OVERVIEW"),
        _record("demand", "DEMAND_HORIZON_SUMMARY", "DERIVED"),
        _record("risk", "STRESS_SHORTAGE_OBSERVED", "RISK_SIGNAL"),
        _record("limit", "CAPACITY_NOT_EVALUATED", "LIMITATION"),
    ])

    assert plan.decision == ["decision"]
    assert plan.main_attention == ["risk"]
    assert plan.limitation == ["limit"]
    assert "demand" in plan.supporting


def test_summary_plan_does_not_invent_attention_when_there_is_no_risk():
    plan = summary_communication_plan([_record("decision", "PLAN_OVERVIEW")])

    assert plan.decision == ["decision"]
    assert plan.main_attention == []
    assert plan.limitation == []


def test_why_plan_only_selects_causal_fact_as_answer_first():
    without_cause = narrative_communication_plan([
        _record("quantity", "PROCUREMENT_QUANTITY"),
        _record("demand", "DEMAND_HORIZON_SUMMARY", "DERIVED"),
    ], "INGREDIENT_NEED")
    with_cause = narrative_communication_plan([
        _record("reason", "PROCUREMENT_REASON", "CAUSAL"),
        _record("quantity", "PROCUREMENT_QUANTITY"),
    ], "INGREDIENT_NEED")

    assert without_cause.decision == []
    assert with_cause.decision == ["reason"]
    assert "quantity" in with_cause.supporting


def test_quantity_and_demand_questions_have_deterministic_primary_fact():
    records = [_record("quantity", "PROCUREMENT_QUANTITY"), _record("demand", "DEMAND_HORIZON_SUMMARY", "DERIVED")]

    assert narrative_communication_plan(records, "INGREDIENT_QUANTITY").decision == ["quantity"]
    assert narrative_communication_plan(records, "INGREDIENT_DEMAND").decision == ["demand"]


def test_what_if_plan_selects_mutation_and_precomputed_outcomes():
    records = [
        {"evidence_id": "mutation", "fact_type": "WHAT_IF_MUTATION"},
        {"evidence_id": "cost", "fact_type": "WHAT_IF_PURCHASE_COST_DELTA"},
        {"evidence_id": "fill", "fact_type": "WHAT_IF_FILL_RATE_DELTA"},
    ]
    plan = what_if_communication_plan(records)

    assert plan.decision == ["mutation"]
    assert plan.main_attention == ["cost", "fill"]
