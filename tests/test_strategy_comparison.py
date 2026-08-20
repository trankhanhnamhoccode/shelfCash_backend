import json
from datetime import date, datetime, timezone

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
