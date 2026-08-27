from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    ProcurementRowBrief,
    RecommendationBrief,
    RiskBrief,
    RiskDetail,
)
from app.decision_intelligence.what_if_evidence import (
    WhatIfNarrativeProvider,
    build_what_if_facts,
)


def _brief(*, cost, quantity, available=True, strategy="balanced", risks=()):
    return DecisionBriefFacts(
        decision_run_id="what-if-run", store_id="STORE_001",
        status="completed" if available else "completed_with_no_feasible_recommendation",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=available, strategy=strategy if available else None, total_purchase_cost=cost if available else None),
        procurement_rows=[] if quantity is None else [ProcurementRowBrief(
            ingredient_id="banana", ingredient_name="Chuoi", quantity=quantity, unit="kg",
        )],
        risk=RiskBrief(), critic=CriticBrief(), risk_details=list(risks),
        generated_at=datetime.now(timezone.utc),
    )


def _package(*, fill, probability):
    return {
        "business_metrics": {"probabilistic": {
            "status": "evaluated", "method": "bootstrap", "metric_source": "stochastic_exact_fefo",
            "expected_fill_rate": fill, "stockout_probability": probability,
        }},
    }


def _body(**changes):
    values = {
        "demand_multiplier": 1.2, "supplier_delay_days": 0,
        "budget_limit": None, "strategy": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _facts(*, body=None, baseline=None, hypothetical=None, baseline_package=None, hypothetical_package=None):
    return build_what_if_facts(
        "what-if-run", baseline or _brief(cost=8_000_000, quantity=30),
        hypothetical or _brief(cost=9_200_000, quantity=35), body or _body(),
        baseline_package=baseline_package or _package(fill=.988, probability=.02),
        hypothetical_package=hypothetical_package or _package(fill=.970, probability=.03),
    )


def test_mutation_and_deltas_are_computed_before_narration():
    facts = _facts()
    comparison = facts.comparison
    banana = comparison.order_changes[0]

    assert facts.mutation.demand_change_ratio == .2
    assert facts.mutation.demand_change_percent == 20.0
    assert comparison.purchase_cost_delta == 1_200_000.0
    assert comparison.expected_fill_rate_delta == -.018
    assert comparison.expected_fill_rate_percentage_point_delta == -1.8
    assert comparison.stockout_probability_delta == .01
    assert banana.ingredient_id == "banana"
    assert banana.unit == "kg"
    assert banana.change_type == "increased"
    assert banana.quantity_delta == 5.0
    assert {item.fact_type for item in facts.evidence} >= {
        "WHAT_IF_MUTATION", "WHAT_IF_PURCHASE_COST_DELTA", "WHAT_IF_FILL_RATE_DELTA", "WHAT_IF_ORDER_CHANGE",
    }


def test_null_probability_never_produces_probability_delta():
    facts = _facts(hypothetical_package=_package(fill=.970, probability=None))
    assert facts.comparison.stockout_probability_delta is None
    assert not [item for item in facts.evidence if item.fact_type == "WHAT_IF_STOCKOUT_PROBABILITY_DELTA"]


class _Gateway:
    available = True

    def __init__(self, response):
        self.response = response
        self.payload = None

    async def generate_json(self, _system, payload, **_kwargs):
        self.payload = payload
        return self.response(payload)


def _valid_response(payload):
    mutation = next(item for item in payload["evidence"] if item["fact_type"] == "WHAT_IF_MUTATION")
    cost = next(item for item in payload["evidence"] if item["fact_type"] == "WHAT_IF_PURCHASE_COST_DELTA")
    fill = next(item for item in payload["evidence"] if item["fact_type"] == "WHAT_IF_FILL_RATE_DELTA")
    return {
        "answer": "Khi nhu cầu tăng 20%, chi phí mua tăng 1.200.000 và mức đáp ứng giảm 1,8 điểm phần trăm.",
        "claims": [
            {"type": "WHAT_IF_MUTATION", "text": "Khi nhu cầu tăng 20%, chi phí mua tăng 1.200.000.", "evidence_ids": [mutation["evidence_id"], cost["evidence_id"]]},
            {"type": "WHAT_IF_MUTATION", "text": "Khi nhu cầu tăng 20%, mức đáp ứng giảm -1,8 điểm phần trăm.", "evidence_ids": [mutation["evidence_id"], fill["evidence_id"]]},
        ],
        "used_evidence_ids": [mutation["evidence_id"], cost["evidence_id"], fill["evidence_id"]],
    }


def test_qwen_receives_precomputed_facts_and_accepts_grounded_intervention_claim():
    facts = _facts()
    gateway = _Gateway(_valid_response)
    response = WhatIfNarrativeProvider(gateway).explain("what-if-run", facts)

    assert response.source == "openrouter_qwen"
    assert response.grounded is True
    assert gateway.payload["intent"] == "WHAT_IF"
    assert gateway.payload["communication_plan"]["mutation"]
    assert gateway.payload["communication_plan"]["primary_outcome"]
    assert all("calculate" not in str(item).lower() for item in gateway.payload["evidence"])


def test_wrong_delta_or_unsupported_mechanism_falls_back():
    facts = _facts()

    def wrong_delta(payload):
        response = _valid_response(payload)
        response["claims"][0]["text"] = "Khi nhu cầu tăng 20%, chi phí mua tăng 1.500.000."
        return response

    assert WhatIfNarrativeProvider(_Gateway(wrong_delta)).explain("what-if-run", facts).source == "deterministic_fallback"

    def pack_mechanism(payload):
        response = _valid_response(payload)
        response["claims"][0]["text"] = "Khi nhu cầu tăng 20%, chi phí mua tăng vì quy cách đóng gói."
        return response

    assert WhatIfNarrativeProvider(_Gateway(pack_mechanism)).explain("what-if-run", facts).source == "deterministic_fallback"

    def answer_only_wrong_number(payload):
        response = _valid_response(payload)
        response["answer"] = "Trong kịch bản này, chi phí mua tăng 1.500.000."
        return response

    assert WhatIfNarrativeProvider(_Gateway(answer_only_wrong_number)).explain("what-if-run", facts).source == "deterministic_fallback"


def test_qwen_timeout_keeps_the_deterministic_what_if_result():
    class TimeoutGateway:
        available = True

        async def generate_json(self, *_args, **_kwargs):
            raise TimeoutError("simulated provider timeout")

    response = WhatIfNarrativeProvider(TimeoutGateway()).explain("what-if-run", _facts())
    assert response.source == "deterministic_fallback"
    assert "Chi phí mua" in response.answer


def test_budget_is_scenario_input_not_binding_cause_and_delay_stays_global():
    facts = _facts(body=_body(budget_limit=5_000_000, supplier_delay_days=1))
    fallback = WhatIfNarrativeProvider(None).explain("what-if-run", facts)
    assert "giới hạn ngân sách" in fallback.answer
    assert "tăng đồng loạt thêm 1 ngày" in fallback.answer

    def budget_binding(payload):
        mutation = next(item for item in payload["evidence"] if item["fact_type"] == "WHAT_IF_MUTATION")
        cost = next(item for item in payload["evidence"] if item["fact_type"] == "WHAT_IF_PURCHASE_COST_DELTA")
        return {
            "answer": "Chi phí tăng vì ngân sách bị chạm trần.",
            "claims": [{"type": "WHAT_IF_MUTATION", "text": "Chi phí tăng vì ngân sách bị chạm trần.", "evidence_ids": [mutation["evidence_id"], cost["evidence_id"]]}],
            "used_evidence_ids": [mutation["evidence_id"], cost["evidence_id"]],
        }

    assert WhatIfNarrativeProvider(_Gateway(budget_binding)).explain("what-if-run", facts).source == "deterministic_fallback"


def test_infeasible_and_zero_change_fallbacks_are_grounded():
    infeasible = _facts(hypothetical=_brief(cost=0, quantity=None, available=False, strategy="balanced"))
    response = WhatIfNarrativeProvider(None).explain("what-if-run", infeasible)
    assert "không tìm được kế hoạch nhập khả thi" in response.answer

    same = _facts(
        body=_body(demand_multiplier=1.0),
        hypothetical=_brief(cost=8_000_000, quantity=30),
        hypothetical_package=_package(fill=.988, probability=.02),
    )
    response = WhatIfNarrativeProvider(None).explain("what-if-run", same)
    assert "không thay đổi" in response.answer


def test_risk_and_limitation_changes_remain_distinct():
    risk = RiskDetail(code="STRESS_SHORTAGE_OBSERVED", classification="risk", category="shortage", severity="warning", title="Stress", scope="run", source_count=1)
    limitation = RiskDetail(code="RISK_METRIC_NOT_AVAILABLE", classification="limitation", category="risk_evaluation", severity="warning", title="Data", scope="run", source_count=1)
    risk_facts = _facts(hypothetical=_brief(cost=9_200_000, quantity=35, risks=[risk]))
    assert risk_facts.comparison.new_issues[0].classification == "risk"
    limitation_facts = _facts(hypothetical=_brief(cost=9_200_000, quantity=35, risks=[limitation]))
    assert limitation_facts.comparison.new_issues[0].classification == "limitation"


def test_raw_machine_code_in_what_if_prose_falls_back():
    def raw_code(payload):
        response = _valid_response(payload)
        response["answer"] = "PACK_SIZE_ROUNDING làm chi phí mua tăng."
        return response

    response = WhatIfNarrativeProvider(_Gateway(raw_code)).explain("what-if-run", _facts())
    assert response.source == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in response.answer
