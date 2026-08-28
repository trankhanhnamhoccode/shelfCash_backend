from app.decision_intelligence.narrative_retrieval import retrieve_narrative_evidence
from decimal import Decimal
from app.decision_intelligence.style_examples import retrieve_style_examples
from app.decision_intelligence.display import add_numeric_display_contract
from app.decision_intelligence.display import vi_number
from app.decision_intelligence.narrative import DecisionNarrativeProvider
import pytest


class _Row:
    def __init__(self, ingredient_id, ingredient_name):
        self.ingredient_id = ingredient_id
        self.ingredient_name = ingredient_name


class _Brief:
    procurement_rows = [_Row("matcha", "Bột matcha"), _Row("milk", "Sữa tươi")]
    ingredient_demand = procurement_rows


def _record(identifier, type_, *, ingredient_id="matcha", classification="OBSERVATION", **values):
    return {"evidence_id": identifier, "type": type_, "ingredient_id": ingredient_id, "classification": classification, **values}


def test_why_retrieval_prioritizes_causal_fact_and_scopes_canonical_ingredient():
    result = retrieve_narrative_evidence(_Brief(), [
        _record("reason", "PROCUREMENT_REASON", classification="CAUSAL"),
        _record("quantity", "PROCUREMENT_QUANTITY"),
        _record("other", "PROCUREMENT_QUANTITY", ingredient_id="milk"),
    ], question="Tại sao cần nhập Bột matcha?", ingredient_id=None, detail_level="simple")

    assert result.intent == "WHY_PROCUREMENT"
    assert result.target_ingredient_id == "matcha"
    assert result.causal_allowed is True
    assert [item["evidence_id"] for item in result.evidence] == ["reason", "quantity"]


def test_why_without_causal_fact_does_not_gain_causal_authority():
    result = retrieve_narrative_evidence(_Brief(), [
        _record("quantity", "PROCUREMENT_QUANTITY"),
        _record("demand", "DEMAND_HORIZON_SUMMARY", classification="DERIVED"),
    ], question="Tại sao cần nhập Bột matcha?", ingredient_id=None, detail_level="simple")

    assert result.causal_allowed is False
    assert [item["evidence_id"] for item in result.evidence] == ["quantity", "demand"]


def test_quantity_day_and_horizon_retrieval_are_type_scoped():
    records = [
        _record("quantity", "PROCUREMENT_QUANTITY"),
        _record("horizon", "DEMAND_HORIZON_SUMMARY"),
        _record("day-14", "DEMAND_DAILY", target_date="2026-08-14"),
        _record("day-15", "DEMAND_DAILY", target_date="2026-08-15"),
    ]
    assert [item["type"] for item in retrieve_narrative_evidence(_Brief(), records, question="Cần nhập bao nhiêu Bột matcha?", ingredient_id=None, detail_level="simple").evidence] == ["PROCUREMENT_QUANTITY"]
    assert [item["evidence_id"] for item in retrieve_narrative_evidence(_Brief(), records, question="Ngày 14/08 cần bao nhiêu Bột matcha?", ingredient_id=None, detail_level="simple").evidence] == ["day-14"]
    assert [item["type"] for item in retrieve_narrative_evidence(_Brief(), records, question="Nhu cầu Bột matcha tuần tới?", ingredient_id=None, detail_level="simple").evidence] == ["DEMAND_HORIZON_SUMMARY"]


def test_style_examples_are_placeholder_only_and_bounded():
    examples = retrieve_style_examples(task="decision_narrative", intent="WHY_PROCUREMENT", case="CAUSAL_UNAVAILABLE", detail_level="simple", limit=3)
    assert len(examples) == 2
    assert all("<" in item["template"] or "Chưa đủ dữ liệu" in item["template"] for item in examples)
    assert all("60" not in item["template"] and "Sữa tươi" not in item["template"] for item in examples)
    assert any(item["negative"] for item in examples)


def test_numeric_display_contract_rejects_invented_rounding_and_accepts_supplied_range():
    record = add_numeric_display_contract({
        "type": "DEMAND_HORIZON_SUMMARY", "daily_p50_min": 157.82,
        "daily_p50_max": 206.36, "unit": "cái",
    })
    supplied = record["display_values"]["daily_demand_range"]
    DecisionNarrativeProvider._validate_numbers(f"Nhu cầu dao động {supplied}.", [record])
    with pytest.raises(ValueError, match="unsupported_numeric_claim"):
        DecisionNarrativeProvider._validate_numbers("Nhu cầu là 157-206 cái/ngày.", [record])
    with pytest.raises(ValueError, match="unsupported_numeric_claim"):
        DecisionNarrativeProvider._validate_numbers("Nhu cầu là 205 cái/ngày.", [record])
    with pytest.raises(ValueError, match="range_semantics_contradicted"):
        DecisionNarrativeProvider._validate_numbers(f"Nhu cầu trung bình là {supplied}.", [record])


def test_style_example_number_cannot_become_numeric_authority():
    record = add_numeric_display_contract({"type": "PROCUREMENT_QUANTITY", "value": 5, "unit": "kg"})
    with pytest.raises(ValueError, match="unsupported_numeric_claim"):
        DecisionNarrativeProvider._validate_numbers("Kế hoạch đề xuất nhập 60 kg.", [record])


def test_display_rounding_is_half_up_and_respects_canonical_piece_unit():
    assert vi_number(Decimal("4.185"), 2) == "4,19"
    assert vi_number(Decimal("2.5"), 0) == "3"
    piece = add_numeric_display_contract({"type": "DEMAND_DAILY", "p50": 157.5, "unit": "cái"})
    continuous = add_numeric_display_contract({"type": "DEMAND_DAILY", "p50": 4.185, "unit": "kg"})
    assert piece["display_values"]["p50"] == "158"
    assert continuous["display_values"]["p50"] == "4,19"
