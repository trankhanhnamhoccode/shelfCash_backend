import json
from datetime import date, datetime, timezone
import pytest

from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    IngredientDemandBrief,
    ProcurementRowBrief,
    RecommendationBrief,
    RiskBrief,
    DecisionOverallSummaryLLMResponse,
)
from app.decision_intelligence.overall_summary import OverallSummaryProvider
from app.decision_intelligence.display import purchase_cost_display
from app.decision_intelligence.style_examples import retrieve_style_examples
from app.decision_intelligence.semantic_evidence import (
    DecisionSemanticEvidenceBuilder, SemanticFact, SemanticFactClassification,
    SemanticFactProvenance, SemanticFactScope,
)
from app.models.decision import DecisionRunModel


def _brief() -> DecisionBriefFacts:
    return DecisionBriefFacts(
        decision_run_id="overall-summary-run",
        store_id="STORE_001",
        status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(
            available=True,
            strategy="balanced",
            total_purchase_cost=8_338_000,
        ),
        procurement_rows=[
            ProcurementRowBrief(
                ingredient_id="banana", ingredient_name="Chuoi", quantity=30,
                unit="kg", pack_count=6, pack_size=5,
                reason_codes=["PACK_SIZE_ROUNDING"],
            )
        ],
        ingredient_demand=[
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 23), p25=8, p50=9.45, p75=10.5),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 21), p25=8, p50=9.5, p75=10),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 22), p25=9, p50=10, p75=11),
        ],
        risk=RiskBrief(stockout_probability=None),
        critic=CriticBrief(),
        generated_at=datetime.now(timezone.utc),
    )


class _Gateway:
    available = True

    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
        self.payload = None

    async def generate_json(self, _system, payload, **_kwargs):
        self.calls += 1
        self.payload = payload
        return self.factory(payload)


def _valid_response(payload):
    by_type = {item["type"]: item for item in payload["evidence"]}
    overview = by_type["PLAN_OVERVIEW"]["evidence_id"]
    alignment = by_type["DEMAND_ORDER_ALIGNMENT"]["evidence_id"]
    headline = {
        "type": "PLAN_OVERVIEW",
        "text": "Ke hoach hien tai su dung chien luoc Can bang.",
        "evidence_ids": [overview],
    }
    comparison = {
        "type": "DEMAND_ORDER_ALIGNMENT",
        "text": "Luong dat 30 kg cao hon tong nhu cau trung vi 28,95 kg khoang 1,05 kg.",
        "evidence_ids": [alignment],
    }
    return {
        "headline": headline,
        "summary": comparison,
        "key_points": [],
        "warning_summary": None,
        "used_evidence_ids": [overview, alignment],
    }


def test_overall_summary_accepts_grounded_observation_and_derived_comparison():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    gateway = _Gateway(_valid_response)

    summary = OverallSummaryProvider(gateway, None).summarize(brief, facts)

    assert gateway.calls == 1
    assert summary.source == "llm"
    assert summary.grounded is True
    assert "1,05" in summary.summary
    assert gateway.payload["communication_plan"]["decision"]
    assert gateway.payload["communication_plan"]["main_attention"] == []


def test_overall_summary_schema_rejects_more_than_three_key_points():
    brief = _brief(); facts = DecisionSemanticEvidenceBuilder().build(brief)
    def verbose(payload):
        response = _valid_response(payload)
        response["key_points"] = [response["summary"] for _ in range(8)]
        return response
    assert OverallSummaryProvider(_Gateway(verbose), None).summarize(brief, facts).source == "deterministic_fallback"


def test_overall_summary_rejects_duplicate_key_point_and_warning_text():
    brief = _brief(); facts = DecisionSemanticEvidenceBuilder().build(brief)
    def duplicate(payload):
        response = _valid_response(payload)
        response["key_points"] = [response["summary"]]
        response["warning_summary"] = response["summary"]
        return response
    assert OverallSummaryProvider(_Gateway(duplicate), None).summarize(brief, facts).source == "deterministic_fallback"


def _provenance_response(evidence_id, text):
    claim = {"type": "INGREDIENT_OPERATIONAL_RISK", "text": text, "evidence_ids": [evidence_id]}
    return DecisionOverallSummaryLLMResponse.model_validate({"headline": claim, "summary": claim, "key_points": [], "warning_summary": None, "used_evidence_ids": [evidence_id]})


def test_conservative_and_stress_provenance_cannot_be_mislabeled_as_selected_plan():
    conservative = {"evidence_id": "c", "type": "INGREDIENT_OPERATIONAL_RISK", "basis_kind": "conservative_design_scenario"}
    stress = {"evidence_id": "s", "type": "STRESS_SHORTAGE_OBSERVED"}
    OverallSummaryProvider._validate_expression(_provenance_response("c", "Trong kịch bản nhu cầu bảo thủ, mô phỏng ghi nhận nguy cơ thiếu."), {"c"}, [conservative])
    OverallSummaryProvider._validate_expression(_provenance_response("s", "Kịch bản kiểm tra sức chịu đựng ghi nhận nguy cơ thiếu."), {"s"}, [stress])
    import pytest
    with pytest.raises(ValueError, match="conservative_mislabeled"):
        OverallSummaryProvider._validate_expression(_provenance_response("c", "Kế hoạch hiện tại dự kiến thiếu."), {"c"}, [conservative])
    with pytest.raises(ValueError, match="stress_mislabeled"):
        OverallSummaryProvider._validate_expression(_provenance_response("s", "Kế hoạch hiện tại thiếu hàng."), {"s"}, [stress])


def test_provenance_validation_is_input_order_independent():
    selected = {"evidence_id": "p", "type": "PLAN_OVERVIEW"}
    conservative = {"evidence_id": "c", "type": "INGREDIENT_OPERATIONAL_RISK", "basis_kind": "conservative_design_scenario"}
    typed = _provenance_response("c", "Trong kịch bản nhu cầu bảo thủ, mô phỏng ghi nhận nguy cơ thiếu.")
    OverallSummaryProvider._validate_expression(typed, {"c"}, [selected, conservative])
    OverallSummaryProvider._validate_expression(typed, {"c"}, [conservative, selected])


def test_overall_summary_runtime_rejects_wrong_fill_rate_term_and_accepts_correct_term():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)

    def provider_response(text):
        def factory(payload):
            response = _valid_response(payload)
            response["summary"]["text"] = text
            return response
        return factory

    invalid = OverallSummaryProvider(
        _Gateway(provider_response("Tỷ lệ lấp kho cần theo dõi.")), None,
    ).summarize(brief, facts)
    valid = OverallSummaryProvider(
        _Gateway(provider_response("Tỷ lệ đáp ứng nhu cầu cần theo dõi.")), None,
    ).summarize(brief, facts)
    assert invalid.source == "deterministic_fallback"
    assert invalid.llm_diagnostics["error_message"] == "overall_summary_invalid_fill_rate_terminology"
    assert valid.source == "llm"


def test_provider_like_future_and_three_provenance_wording_is_accepted_and_order_independent():
    selected = {"evidence_id": "p", "type": "PLAN_OVERVIEW"}
    conservative = {"evidence_id": "c", "type": "INGREDIENT_OPERATIONAL_RISK", "basis_kind": "conservative_design_scenario"}
    stress = {"evidence_id": "s", "type": "STRESS_SHORTAGE_OBSERVED"}
    claims = [
        {"type": "PLAN_OVERVIEW", "text": "ShelfCash đề xuất phương án hiện tại.", "evidence_ids": ["p"]},
        {"type": "INGREDIENT_OPERATIONAL_RISK", "text": "Trong kịch bản nhu cầu bảo thủ, mô phỏng ghi nhận nguy cơ thiếu hụt.", "evidence_ids": ["c"]},
        {"type": "STRESS_SHORTAGE_OBSERVED", "text": "Một số kịch bản kiểm tra sức chịu đựng ghi nhận nguy cơ thiếu hụt.", "evidence_ids": ["s"]},
    ]
    typed = DecisionOverallSummaryLLMResponse.model_validate({
        "headline": claims[0], "summary": claims[1], "key_points": [claims[2]],
        "warning_summary": None, "used_evidence_ids": ["p", "c", "s"],
    })
    OverallSummaryProvider._validate_expression(typed, {"p", "c", "s"}, [selected, conservative, stress])
    OverallSummaryProvider._validate_expression(typed, {"p", "c", "s"}, [stress, selected, conservative])


def test_valid_key_point_counts_are_schema_and_business_valid():
    claim = {"type": "PLAN_OVERVIEW", "text": "Kế hoạch hiện tại đã được chọn.", "evidence_ids": ["p"]}
    for count in range(4):
        typed = DecisionOverallSummaryLLMResponse.model_validate({
            "headline": claim,
            "summary": {**claim, "text": "ShelfCash đề xuất kế hoạch hiện tại."},
            "key_points": [{**claim, "text": f"Điểm cần theo dõi {index}."} for index in range(count)],
            "warning_summary": None,
            "used_evidence_ids": ["p"],
        })
        OverallSummaryProvider._validate_expression(typed, {"p"}, [{"evidence_id": "p", "type": "PLAN_OVERVIEW"}])


def _risk_fact(fact_type, *, basis_kind=None):
    values = {"basis_kind": basis_kind} if basis_kind else {}
    return SemanticFact(
        fact_id=fact_type, fact_type=fact_type, decision_run_id="overall-summary-run",
        classification=SemanticFactClassification.RISK_SIGNAL, scope=SemanticFactScope.INGREDIENT,
        values=values,
        provenance=SemanticFactProvenance(source_type="test", source_module="test", source_path="test"),
    )


def test_deterministic_fallback_preserves_selected_conservative_and_stress_provenance():
    provider = OverallSummaryProvider(None, None)
    selected = provider.deterministic_fallback(_brief(), [_risk_fact("SELECTED_PLAN_RISK_METRICS")])
    conservative = provider.deterministic_fallback(_brief(), [_risk_fact("INGREDIENT_OPERATIONAL_RISK", basis_kind="conservative_design_scenario")])
    stress = provider.deterministic_fallback(_brief(), [_risk_fact("STRESS_SHORTAGE_OBSERVED")])
    assert "Kế hoạch hiện tại có" in " ".join(selected.key_points)
    assert "kịch bản nhu cầu bảo thủ" in " ".join(conservative.key_points)
    assert "kịch bản kiểm tra" in " ".join(stress.key_points)


def test_conservative_payload_exposes_role_provenance_exact_display_and_matching_example():
    provenance = SemanticFactProvenance(source_type="test", source_module="test", source_path="test")
    facts = [
        SemanticFact(
            fact_id="plan", fact_type="PLAN_OVERVIEW", decision_run_id="overall-summary-run",
            classification=SemanticFactClassification.OBSERVATION, scope=SemanticFactScope.RUN,
            values={"strategy": "balanced", "horizon_days": 7, "ordered_ingredient_count": 1, "total_purchase_cost": 8338000}, provenance=provenance,
        ),
        SemanticFact(
            fact_id="risk", fact_type="INGREDIENT_OPERATIONAL_RISK", decision_run_id="overall-summary-run",
            classification=SemanticFactClassification.RISK_SIGNAL, scope=SemanticFactScope.INGREDIENT,
            entities={"ingredient_id": "banana"},
            values={"ingredient_name": "Chuối", "basis_kind": "conservative_design_scenario", "shortage_quantity": 3, "fill_rate": .7394, "first_stockout_date": "2026-08-14"}, provenance=provenance,
        ),
        SemanticFact(
            fact_id="limit", fact_type="CAPACITY_NOT_EVALUATED", decision_run_id="overall-summary-run",
            classification=SemanticFactClassification.LIMITATION, scope=SemanticFactScope.RUN,
            values={"code": "CAPACITY_NOT_EVALUATED"}, provenance=provenance,
        ),
    ]
    gateway = _Gateway(lambda _payload: {"bad": "response"})
    OverallSummaryProvider(gateway, None).summarize(_brief(), facts)
    payload = gateway.payload
    roles = payload["communication_plan"]["presentation_roles"]
    assert roles["main_risk"]["presentation_provenance"] == "CONSERVATIVE_DESIGN"
    assert roles["main_risk"]["evidence_ids"]
    assert payload["communication_plan"]["causal_allowed"] is False
    assert payload["style_examples"][0]["example_id"] == "summary-conservative-design-risk"
    risk = next(item for item in payload["evidence"] if item["type"] == "INGREDIENT_OPERATIONAL_RISK")
    assert risk["display_values"]["fill_rate"] == "73,94%"


def test_overall_summary_examples_are_provenance_specific_and_deterministic():
    cases = {
        "SELECTED_PLAN_RISK": "summary-selected-plan-risk",
        "CONSERVATIVE_DESIGN_RISK": "summary-conservative-design-risk",
        "STRESS_RISK": "summary-stress-risk",
        "WITH_LIMITATION": "summary-limit",
    }
    for case, example_id in cases.items():
        first = retrieve_style_examples(task="overall_summary", intent="SUMMARY", case=case, detail_level="simple")
        assert first == retrieve_style_examples(task="overall_summary", intent="SUMMARY", case=case, detail_level="simple")
        assert first[0]["example_id"] == example_id
    assert "kịch bản nhu cầu bảo thủ" in retrieve_style_examples(task="overall_summary", intent="SUMMARY", case="CONSERVATIVE_DESIGN_RISK", detail_level="simple")[0]["template"]
    assert "vượt công suất" not in retrieve_style_examples(task="overall_summary", intent="SUMMARY", case="WITH_LIMITATION", detail_level="simple")[0]["template"]


@pytest.mark.parametrize(("risk_type", "risk_values", "expected_provenance", "risk_text"), [
    ("INGREDIENT_OPERATIONAL_RISK", {"basis_kind": "conservative_design_scenario", "fill_rate": .7394, "first_stockout_date": "2026-08-14"}, "CONSERVATIVE_DESIGN", "Trong kịch bản nhu cầu bảo thủ, mô phỏng ghi nhận nguy cơ thiếu từ 14/08. Tỷ lệ đáp ứng nhu cầu là 73,94%."),
    ("STRESS_SHORTAGE_OBSERVED", {}, "STRESS", "Trong kịch bản kiểm tra sức chịu đựng, mô phỏng ghi nhận nguy cơ thiếu."),
    ("SELECTED_PLAN_RISK_METRICS", {}, "SELECTED_PLAN", "Kế hoạch hiện tại có một nguy cơ thiếu cần theo dõi."),
])
def test_provider_like_provenance_matched_risks_are_accepted(risk_type, risk_values, expected_provenance, risk_text):
    provenance = SemanticFactProvenance(source_type="test", source_module="test", source_path="test")
    facts = [
        SemanticFact(fact_id="plan", fact_type="PLAN_OVERVIEW", decision_run_id="overall-summary-run", classification=SemanticFactClassification.OBSERVATION, scope=SemanticFactScope.RUN, values={"strategy": "balanced"}, provenance=provenance),
        SemanticFact(fact_id="risk", fact_type=risk_type, decision_run_id="overall-summary-run", classification=SemanticFactClassification.RISK_SIGNAL, scope=SemanticFactScope.INGREDIENT, entities={"ingredient_id": "banana"}, values=risk_values, provenance=provenance),
    ]
    def response(payload):
        by_type = {item["type"]: item["evidence_id"] for item in payload["evidence"]}
        plan_id, risk_id = by_type["PLAN_OVERVIEW"], by_type[risk_type]
        return {
            "headline": {"type": "PLAN_OVERVIEW", "text": "ShelfCash đề xuất phương án hiện tại.", "evidence_ids": [plan_id]},
            "summary": {"type": risk_type, "text": risk_text, "evidence_ids": [risk_id]},
            "key_points": [], "warning_summary": None, "used_evidence_ids": [plan_id, risk_id],
        }
    gateway = _Gateway(response)
    summary = OverallSummaryProvider(gateway, None).summarize(_brief(), facts)
    assert gateway.payload["communication_plan"]["presentation_roles"]["main_risk"]["presentation_provenance"] == expected_provenance
    assert summary.source == "llm"


@pytest.mark.parametrize(("text", "expected_source"), [
    ("Khả năng lưu trữ toàn kho hiện chưa được đánh giá đầy đủ.", "llm"),
    ("Kho đã vượt công suất.", "deterministic_fallback"),
    ("Khả năng lưu trữ chưa được đánh giá đầy đủ, nhưng kho đã vượt công suất.", "deterministic_fallback"),
])
def test_capacity_not_evaluated_is_not_mislabeled_as_capacity_failure(text, expected_source):
    provenance = SemanticFactProvenance(source_type="test", source_module="test", source_path="test")
    facts = [
        SemanticFact(fact_id="plan", fact_type="PLAN_OVERVIEW", decision_run_id="overall-summary-run", classification=SemanticFactClassification.OBSERVATION, scope=SemanticFactScope.RUN, values={"strategy": "balanced"}, provenance=provenance),
        SemanticFact(fact_id="limit", fact_type="CAPACITY_NOT_EVALUATED", decision_run_id="overall-summary-run", classification=SemanticFactClassification.LIMITATION, scope=SemanticFactScope.RUN, values={"code": "CAPACITY_NOT_EVALUATED"}, provenance=provenance),
    ]
    def response(payload):
        by_type = {item["type"]: item["evidence_id"] for item in payload["evidence"]}
        return {
            "headline": {"type": "PLAN_OVERVIEW", "text": "ShelfCash đề xuất phương án hiện tại.", "evidence_ids": [by_type["PLAN_OVERVIEW"]]},
            "summary": {"type": "CAPACITY_NOT_EVALUATED", "text": text, "evidence_ids": [by_type["CAPACITY_NOT_EVALUATED"]]},
            "key_points": [], "warning_summary": None,
            "used_evidence_ids": [by_type["PLAN_OVERVIEW"], by_type["CAPACITY_NOT_EVALUATED"]],
        }
    summary = OverallSummaryProvider(_Gateway(response), None).summarize(_brief(), facts)
    assert summary.source == expected_source
    if expected_source == "deterministic_fallback":
        assert summary.llm_diagnostics["error_message"] == "overall_summary_capacity_not_evaluated_mislabeled_as_exceeded"


def test_capacity_failure_words_remain_allowed_without_capacity_not_evaluated_authority():
    claim = {"type": "STRESS_CAPACITY_VIOLATION", "text": "Trong kịch bản kiểm tra, kho đã vượt công suất.", "evidence_ids": ["stress"]}
    typed = DecisionOverallSummaryLLMResponse.model_validate({"headline": claim, "summary": claim, "key_points": [], "warning_summary": None, "used_evidence_ids": ["stress"]})
    OverallSummaryProvider._validate_expression(typed, {"stress"}, [{"evidence_id": "stress", "type": "STRESS_CAPACITY_VIOLATION"}])


def test_overall_summary_falls_back_for_malformed_or_unsupported_causal_output():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)

    malformed = OverallSummaryProvider(_Gateway(lambda _payload: {"bad": "response"}), None).summarize(brief, facts)
    assert malformed.source == "deterministic_fallback"

    def unsupported(payload):
        response = _valid_response(payload)
        alignment = next(item for item in payload["evidence"] if item["type"] == "DEMAND_ORDER_ALIGNMENT")
        response["summary"] = {
            "type": "DEMAND_ORDER_ALIGNMENT",
            "text": "30 kg duoc dat do quy cach dong goi 5 kg.",
            "evidence_ids": [alignment["evidence_id"]],
        }
        response["used_evidence_ids"] = [
            response["headline"]["evidence_ids"][0], alignment["evidence_id"],
        ]
        return response

    rejected = OverallSummaryProvider(_Gateway(unsupported), None).summarize(brief, facts)
    assert rejected.source == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in rejected.summary
    assert "quy cach" not in rejected.summary.lower()

    def presentation_claim_type(payload):
        response = _valid_response(payload)
        response["summary"]["type"] = "SUMMARY"
        return response

    rejected_presentation_type = OverallSummaryProvider(_Gateway(presentation_claim_type), None).summarize(brief, facts)
    assert rejected_presentation_type.source == "deterministic_fallback"

    def raw_machine_code(payload):
        response = _valid_response(payload)
        response["summary"] = {
            "type": "DEMAND_ORDER_ALIGNMENT",
            "text": "PACK_SIZE_ROUNDING applies to the plan.",
            "evidence_ids": [next(item for item in payload["evidence"] if item["type"] == "DEMAND_ORDER_ALIGNMENT")["evidence_id"]],
        }
        response["used_evidence_ids"] = [
            response["headline"]["evidence_ids"][0], response["summary"]["evidence_ids"][0],
        ]
        return response

    raw_code = OverallSummaryProvider(_Gateway(raw_machine_code), None).summarize(brief, facts)
    assert raw_code.source == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in raw_code.summary


def test_deterministic_summary_never_invents_null_risk_or_internal_identifiers():
    brief = _brief()
    summary = OverallSummaryProvider(None, None).deterministic_fallback(
        brief, DecisionSemanticEvidenceBuilder().build(brief),
    )

    rendered = " ".join([summary.headline, summary.summary, *summary.key_points, summary.warning_summary or ""])
    assert "overall-summary-run" not in rendered
    assert "PACK_SIZE_ROUNDING" not in rendered
    assert "0%" not in rendered


def test_purchase_cost_display_is_shared_by_fallback_and_llm_evidence():
    brief = _brief().model_copy(update={
        "recommendation": RecommendationBrief(available=True, strategy="balanced", total_purchase_cost=7_668_000),
    })
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    provider = OverallSummaryProvider(None, None)
    fallback = provider.deterministic_fallback(brief, facts)
    _, records = provider._context(brief, facts)
    overview = next(record for record in records if record["type"] == "PLAN_OVERVIEW")

    assert purchase_cost_display(7_668_000) == "7,67 triệu đồng"
    assert overview["total_purchase_cost_display"] == "7,67 triệu đồng"
    assert "7,67 triệu đồng" in fallback.summary
    assert "7.668. đồng" not in fallback.summary


def test_operational_risk_date_gets_a_backend_display_value_for_grounding():
    record = {"first_stockout_date": "2026-08-14"}
    OverallSummaryProvider._add_display_values(record)

    assert record["first_stockout_date_display"] == "14/08"


def test_no_feasible_recommendation_has_a_grounded_summary_without_order_metrics():
    brief = _brief().model_copy(update={
        "status": "completed_with_no_feasible_recommendation",
        "recommendation": RecommendationBrief(available=False),
        "procurement_rows": [],
    })
    summary = OverallSummaryProvider(None, None).deterministic_fallback(
        brief, DecisionSemanticEvidenceBuilder().build(brief),
    )

    assert summary.source == "deterministic_fallback"
    assert summary.grounded is True
    assert "30" not in summary.summary
    assert "8.338" not in summary.summary


def _run(run_id: str, package: dict) -> DecisionRunModel:
    return DecisionRunModel(
        decision_run_id=run_id,
        store_id="STORE_001",
        forecast_run_id="missing-forecast",
        as_of_date=date(2026, 8, 20),
        horizon_days=7,
        engine_mode="deterministic",
        status=package["status"],
        scenario_method="test",
        scenario_count=1,
        random_seed=42,
        recommended_strategy=package.get("recommended_strategy"),
        request_json="{}",
        package_json=json.dumps(package),
        warnings_json="[]",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def test_brief_adds_canonical_horizon_summary_and_old_run_fallback_without_gateway(client):
    package = {
        "decision_run_id": "phase2-old-run",
        "store_id": "STORE_001",
        "status": "completed",
        "recommended_strategy": "balanced",
        "recommended_plan": {"items": [{"ingredient_id": "banana", "order_quantity": 30, "unit": "kg"}]},
        "ingredient_demand": [
            {"ingredient_id": "banana", "target_date": "2026-08-23", "unit": "kg", "p25": 8, "p50": 9.45, "p75": 10.5},
            {"ingredient_id": "banana", "target_date": "2026-08-21", "unit": "kg", "p25": 8, "p50": 9.5, "p75": 10},
            {"ingredient_id": "banana", "target_date": "2026-08-22", "unit": "kg", "p25": 9, "p50": 10, "p75": 11},
        ],
        "business_metrics": {"projected_purchase_cost": 8_338_000},
        "inventory_risk": {},
        "critic": {"findings": [], "warnings": []},
        "reason_codes": [],
        "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("phase2-old-run", package))
        session.commit()

    first = client.get("/api/v1/decision-runs/phase2-old-run/brief")
    second = client.get("/api/v1/decision-runs/phase2-old-run/brief")
    assert first.status_code == second.status_code == 200
    body = first.json()
    assert [row["target_date"] for row in body["ingredient_demand"]] == ["2026-08-21", "2026-08-22", "2026-08-23"]
    assert body["ingredient_demand_summary"] == [{
        "ingredient_id": "banana", "ingredient_name": None, "unit": "kg",
        "period_start": "2026-08-21", "period_end": "2026-08-23",
        "p25_total": 25.0, "p50_total": 28.95, "p75_total": 31.5,
        "daily_p50_min": 9.45, "daily_p50_max": 10.0,
        "peak_date": "2026-08-22", "peak_p50": 10.0,
        "aggregation_method": "sum_daily_quantiles",
    }]
    assert body["assistant_summary"]["source"] == "deterministic_fallback"
    assert "assistant" not in client.get("/api/v1/decision-runs/phase2-old-run").json()


def test_summary_generation_is_not_repeated_by_brief_reads(client, monkeypatch):
    from app.decision_intelligence.overall_summary import OverallSummaryProvider

    calls = 0
    original = OverallSummaryProvider.summarize

    def counted(self, brief, facts):
        nonlocal calls
        calls += 1
        return original(self, brief, facts)

    monkeypatch.setattr(OverallSummaryProvider, "summarize", counted)
    package = {
        "decision_run_id": "phase2-persisted-run", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [], "business_metrics": {}, "inventory_risk": {},
        "critic": {"findings": [], "warnings": []}, "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("phase2-persisted-run", package))
        session.commit()

    service = client.app.state.decision_planning_service
    service._generate_and_persist_overall_summary("phase2-persisted-run")
    client.get("/api/v1/decision-runs/phase2-persisted-run/brief")
    client.get("/api/v1/decision-runs/phase2-persisted-run/brief")
    assert calls == 1
