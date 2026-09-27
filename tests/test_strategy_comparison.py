import json
from datetime import date, datetime, timezone

import pytest

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    RecommendationBrief,
    RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider, aggregate_evidence
from app.decision_intelligence.semantic_evidence import (
    DecisionSemanticEvidenceBuilder,
    SemanticFactClassification,
)
from app.decision_intelligence.strategy_comparison import project_strategy_comparison
from app.models.decision import DecisionRunModel


def _brief() -> DecisionBriefFacts:
    return DecisionBriefFacts(
        decision_run_id="strategy-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _candidate(*, strategy, feasible, cost, fill, probability):
    return {
        "strategy": strategy,
        "is_feasible": feasible,
        "purchase_cost": cost,
        "business_metrics": {
            "projected_purchase_cost": cost,
            "probabilistic": {
                "status": "evaluated", "method": "bootstrap",
                "metric_source": "stochastic_exact_fefo",
                "expected_fill_rate": fill,
                "stockout_probability": probability,
            },
        },
        "critic": {"findings": [] if feasible else [{"code": "SERVICE_LEVEL_REQUIREMENT", "severity": "error"}], "warnings": []},
        "stress_tests": {"results": []},
    }


def _package(*, selection=True, null_probability=False):
    strategies = {
        "lean": _candidate(strategy="lean", feasible=False, cost=80, fill=.90, probability=.08),
        "balanced": _candidate(strategy="balanced", feasible=True, cost=100, fill=.95, probability=.02),
        "protected": _candidate(strategy="protected", feasible=True, cost=120, fill=.98, probability=None if null_probability else .01),
    }
    package = {"recommended_strategy": "balanced", "strategies": strategies}
    if selection:
        package["strategy_selection"] = {
            "rule": "lowest_valid_candidate_cost_then_strategy_name",
            "selected_strategy": "balanced",
            "eligible_candidates": ["balanced", "protected"],
        }
    return package


def test_candidate_facts_and_selected_relative_deltas_are_deterministic():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    candidates = [fact for fact in facts if fact.fact_type == "STRATEGY_CANDIDATE_METRICS"]
    comparisons = [fact for fact in facts if fact.fact_type == "STRATEGY_COMPARISON"]
    projection = project_strategy_comparison(brief, facts)

    assert len(candidates) == 3
    assert all(fact.classification is SemanticFactClassification.OBSERVATION for fact in candidates)
    protected = next(fact for fact in comparisons if fact.entities["right_strategy"] == "protected")
    assert protected.values["left_strategy"] == "balanced"
    assert protected.values["purchase_cost_delta"] == -20.0
    assert protected.values["expected_fill_rate_delta"] == -.03
    assert protected.values["expected_fill_rate_percentage_point_delta"] == -3.0
    assert projection is not None
    assert [candidate.strategy for candidate in projection.candidates] == ["lean", "balanced", "protected"]
    assert projection.candidates[2].vs_selected.purchase_cost_delta == -20.0


def test_probability_delta_is_unavailable_when_candidate_metric_is_null():
    facts = DecisionSemanticEvidenceBuilder().build(_brief(), _package(null_probability=True))
    protected = next(
        fact for fact in facts
        if fact.fact_type == "STRATEGY_COMPARISON" and fact.entities["right_strategy"] == "protected"
    )
    assert protected.values["stockout_probability_delta"] is None


def test_selection_proof_requires_persisted_rule_and_exact_reconciliation():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    proof = next(fact for fact in facts if fact.fact_type == "STRATEGY_SELECTION_PROOF")
    assert proof.classification is SemanticFactClassification.CAUSAL
    assert proof.values["selected_strategy"] == "balanced"
    assert proof.values["eligible_strategies"] == ["balanced", "protected"]
    assert project_strategy_comparison(brief, facts).selection_reason.available is True

    inconsistent = _package()
    inconsistent["strategy_selection"]["selected_strategy"] = "protected"
    rejected = DecisionSemanticEvidenceBuilder().build(brief, inconsistent)
    assert not [fact for fact in rejected if fact.fact_type == "STRATEGY_SELECTION_PROOF"]
    assert project_strategy_comparison(brief, rejected).selection_reason.available is False


def test_strategy_qwen_claims_accept_comparison_and_reject_unproved_selection_reason():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    structured = aggregate_evidence(brief, evidence.items, semantic_facts=facts)
    comparison = next(item for item in structured if item["type"] == "STRATEGY_COMPARISON" and item["right_strategy"] == "protected")
    proof = next(item for item in structured if item["type"] == "STRATEGY_SELECTION_PROOF")
    provider = DecisionNarrativeProvider(None, None)
    raw = {
        "answer": "Cân bằng có chi phí mua thấp hơn An toàn. Cân bằng được chọn vì có chi phí mua thấp nhất trong các phương án khả thi.",
        "claims": [
            {"type": "STRATEGY_COMPARISON", "text": "Cân bằng có chi phí mua thấp hơn An toàn.", "evidence_ids": [comparison["evidence_id"]]},
            {"type": "STRATEGY_SELECTION_PROOF", "text": "Cân bằng được chọn vì có chi phí mua thấp nhất trong các phương án khả thi.", "evidence_ids": [proof["evidence_id"]]},
        ],
        "used_evidence_ids": [comparison["evidence_id"], proof["evidence_id"]],
    }
    response = provider._guard(raw, structured, evidence.items, brief, "vi", "simple", "generic")
    assert response.grounded is True

    raw["claims"][1]["text"] = "Cân bằng được chọn vì có fill rate cao nhất."
    try:
        provider._guard(raw, structured, evidence.items, brief, "vi", "simple", "generic")
    except ValueError as exc:
        assert str(exc) == "unsupported_strategy_selection_reason"
    else:
        raise AssertionError("selection reason not supported by proof was accepted")


def test_slice_d_guard_accepts_direct_pairwise_multi_fact_and_selection_proof_claims():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    structured = aggregate_evidence(brief, evidence.items, semantic_facts=facts)
    protected = next(item for item in structured if item["type"] == "STRATEGY_COMPARISON" and item["right_strategy"] == "protected")
    lean = next(item for item in structured if item["type"] == "STRATEGY_COMPARISON" and item["right_strategy"] == "lean")
    proof = next(item for item in structured if item["type"] == "STRATEGY_SELECTION_PROOF")
    overview = next(item for item in structured if item["type"] == "PLAN_OVERVIEW")
    provider = DecisionNarrativeProvider(None, None)

    direct = {"answer": "Balanced is selected.", "claims": [{"type": "PLAN_OVERVIEW", "text": "Balanced is selected.", "evidence_ids": [overview["evidence_id"]]}], "used_evidence_ids": [overview["evidence_id"]]}
    assert provider._guard(direct, structured, evidence.items, brief, "en", "simple", "PLAN_SELECTION").grounded is True

    synthesis_text = "Balanced has lower stockout probability than Lean and lower purchase cost than Protected."
    synthesis = {"answer": synthesis_text, "claims": [{"type": "STRATEGY_COMPARISON", "text": synthesis_text, "evidence_ids": [lean["evidence_id"], protected["evidence_id"]]}], "used_evidence_ids": [lean["evidence_id"], protected["evidence_id"]]}
    assert provider._guard(synthesis, structured, evidence.items, brief, "en", "simple", "PLAN_TRADEOFF").grounded is True

    cause_text = "Balanced was selected because it has the lowest purchase cost among eligible candidates."
    cause = {"answer": cause_text, "claims": [{"type": "STRATEGY_SELECTION_PROOF", "text": cause_text, "evidence_ids": [proof["evidence_id"]]}], "used_evidence_ids": [proof["evidence_id"]]}
    assert provider._guard(cause, structured, evidence.items, brief, "en", "simple", "PLAN_SELECTION").grounded is True


@pytest.mark.parametrize(("text", "ids", "error"), [
    ("Protected has lower purchase cost than Balanced.", ("protected",), "unsupported_comparative_claim"),
    ("Balanced has higher fill rate than Protected.", ("protected",), "unsupported_comparative_claim"),
    ("Balanced has lower purchase cost than Protected.", ("protected", "lean"), "unsupported_comparative_claim"),
    ("Balanced is the cheapest option.", ("protected",), "unsupported_strategy_ranking"),
    ("Balanced was selected because it has lower purchase cost than Protected.", ("protected",), "unsupported_causal_claim"),
])
def test_slice_d_guard_rejects_unproved_strategy_semantics(text, ids, error):
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    structured = aggregate_evidence(brief, evidence.items, semantic_facts=facts)
    comparisons = {
        item["right_strategy"]: item for item in structured if item["type"] == "STRATEGY_COMPARISON"
    }
    evidence_ids = [comparisons[strategy]["evidence_id"] for strategy in ids]
    raw = {"answer": text, "claims": [{"type": "STRATEGY_COMPARISON", "text": text, "evidence_ids": evidence_ids}], "used_evidence_ids": evidence_ids}
    with pytest.raises(ValueError, match=error):
        DecisionNarrativeProvider(None, None)._guard(raw, structured, evidence.items, brief, "en", "simple", "PLAN_TRADEOFF")


class _Gateway:
    available = True

    async def generate_json(self, _system, payload, **_kwargs):
        comparison = next(
            item for item in payload["evidence"]
            if item["type"] == "STRATEGY_COMPARISON" and item["right_strategy"] == "protected"
        )
        return {
            "answer": "Cân bằng có chi phí mua thấp hơn An toàn.",
            "claims": [{
                "type": "STRATEGY_COMPARISON",
                "text": "Cân bằng có chi phí mua thấp hơn An toàn.",
                "evidence_ids": [comparison["evidence_id"]],
            }],
            "used_evidence_ids": [comparison["evidence_id"]],
        }


def test_on_demand_strategy_question_uses_canonical_strategy_comparison_evidence():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    response = DecisionNarrativeProvider(_Gateway(), None).explain(
        brief,
        question="Protected khác Balanced thế nào?",
        language="vi",
        detail_level="simple",
        semantic_facts=facts,
    )

    assert response.source == "openrouter_qwen"
    assert response.grounded is True
    assert response.claims[0].type == "STRATEGY_COMPARISON"


@pytest.mark.parametrize("gateway", [
    type("Unavailable", (), {"available": False})(),
    type("Broken", (), {"available": True, "generate_json": staticmethod(lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))})(),
    type("Malformed", (), {"available": True, "generate_json": staticmethod(lambda *_args, **_kwargs: {"answer": "bad", "claims": "bad"})})(),
])
def test_slice_e_qwen_failure_retains_valid_selection_proof(gateway):
    brief = _brief(); facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    response = DecisionNarrativeProvider(gateway, None).explain(
        brief, question="Tại sao chọn kế hoạch này?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="plan_strategy",
    )
    assert "BALANCED" in response.answer
    assert "chi phí mua thấp nhất" in response.answer
    assert "Không đủ dữ liệu để xác nhận" not in response.answer


def test_slice_e_missing_proof_and_comparison_fallback_remain_scoped():
    brief = _brief(); facts = DecisionSemanticEvidenceBuilder().build(brief, _package(selection=False))
    missing = DecisionNarrativeProvider(None, None).explain(
        brief, question="Tại sao chọn kế hoạch này?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="plan_strategy",
    )
    assert "BALANCED là phương án được chọn" in missing.answer
    assert "không đủ để xác nhận lý do" in missing.answer

    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    comparison = DecisionNarrativeProvider(None, None).explain(
        brief, question="Protected khác Balanced thế nào?", language="vi", detail_level="simple",
        semantic_facts=facts, question_scope="plan_strategy",
    )
    assert "BALANCED có chi phí mua thấp hơn PROTECTED" in comparison.answer


def test_brief_exposes_additive_strategy_comparison_and_old_runs_remain_readable(client):
    package = {
        "decision_run_id": "strategy-brief-run", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [], "business_metrics": {}, "inventory_risk": {},
        "critic": {"findings": [], "warnings": []}, "warnings": [], "reason_codes": [],
        **_package(),
    }
    package["decision_run_id"] = "strategy-brief-run"
    with client.app.state.session_factory() as session:
        session.add(DecisionRunModel(
            decision_run_id="strategy-brief-run", store_id="STORE_001",
            forecast_run_id="missing-forecast", as_of_date=date(2026, 8, 20),
            horizon_days=7, engine_mode="deterministic", status="completed",
            scenario_method="test", scenario_count=1, random_seed=42,
            recommended_strategy="balanced", request_json="{}", package_json=json.dumps(package),
            warnings_json="[]", created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
        ))
        session.commit()
    response = client.get("/api/v1/decision-runs/strategy-brief-run/brief")
    assert response.status_code == 200
    comparison = response.json()["strategy_comparison"]
    assert comparison["selected_strategy"] == "balanced"
    assert comparison["selection_reason"]["available"] is True
    assert comparison["candidates"][2]["vs_selected"]["purchase_cost_delta"] == -20.0

    old = _brief().model_copy(update={"strategy_comparison": None})
    assert old.strategy_comparison is None
