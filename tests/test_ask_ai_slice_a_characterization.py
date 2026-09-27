"""R7 Ask-AI characterization and question-scope regression coverage."""

from datetime import date, datetime, timezone

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import CriticBrief, DecisionBriefFacts, ForecastBrief, RecommendationBrief, RiskBrief
from app.decision_intelligence.narrative import DecisionNarrativeProvider, aggregate_evidence
from app.decision_intelligence.narrative_retrieval import retrieve_narrative_evidence
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder, SemanticFactClassification
from app.services.decision.explanation_query_interpretation import QuestionScope, classify_question_scope


def _brief():
    return DecisionBriefFacts(
        decision_run_id="slice-a-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 9, 26)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def _candidate(strategy, feasible, cost, fill, probability):
    return {
        "strategy": strategy, "is_feasible": feasible, "purchase_cost": cost,
        "business_metrics": {"probabilistic": {
            "status": "evaluated", "metric_source": "stochastic_exact_fefo",
            "expected_fill_rate": fill, "stockout_probability": probability,
        }},
        "critic": {"findings": [] if feasible else [{"code": "SERVICE_LEVEL_REQUIREMENT"}], "warnings": []},
        "stress_tests": {"results": []},
    }


def _package():
    return {
        "recommended_strategy": "balanced",
        "strategies": {
            "lean": _candidate("lean", False, 80, 0.90, 0.08),
            "balanced": _candidate("balanced", True, 100, 0.95, 0.02),
            "protected": _candidate("protected", True, 120, 0.98, 0.01),
        },
        "strategy_selection": {
            "rule": "lowest_valid_candidate_cost_then_strategy_name",
            "selected_strategy": "balanced",
            "eligible_candidates": ["balanced", "protected"],
        },
    }


def _structured_facts():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief, _package())
    evidence = ShelfCashDecisionIntelligenceAdapter()._evidence(brief, semantic_facts=facts)
    return brief, facts, evidence, aggregate_evidence(brief, evidence.items, semantic_facts=facts)


def test_plan_selection_scope_drives_strategy_selection_evidence_not_procurement_why():
    question = "Tại sao chọn kế hoạch này?"
    assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY

    brief, _facts, _evidence, structured = _structured_facts()
    retrieval = retrieve_narrative_evidence(
        brief, structured, question=question, ingredient_id=None, detail_level="simple",
        question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    assert retrieval.intent == "PLAN_SELECTION"
    assert retrieval.causal_allowed is True
    types = [item["type"] for item in retrieval.evidence]
    assert types[:2] == ["PLAN_OVERVIEW", "STRATEGY_SELECTION_PROOF"]
    assert "STRATEGY_COMPARISON" in types
    assert "PROCUREMENT_QUANTITY" not in types
    assert "DEMAND_HORIZON_SUMMARY" not in types


def test_plan_selection_missing_proof_does_not_create_cause_or_procurement_substitute():
    brief, _facts, _evidence, structured = _structured_facts()
    without_proof = [item for item in structured if item["type"] != "STRATEGY_SELECTION_PROOF"]

    retrieval = retrieve_narrative_evidence(
        brief, without_proof, question="Tại sao chọn kế hoạch này?", ingredient_id=None,
        detail_level="simple", question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    assert retrieval.intent == "PLAN_SELECTION"
    assert retrieval.causal_allowed is False
    assert any(item["type"] == "PLAN_OVERVIEW" for item in retrieval.evidence)
    assert not any(item["type"] == "STRATEGY_SELECTION_PROOF" for item in retrieval.evidence)
    assert not any(item["type"] in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY"} for item in retrieval.evidence)


def test_plan_strategy_retrieval_limits_named_alternative_to_lean():
    brief, _facts, _evidence, structured = _structured_facts()
    retrieval = retrieve_narrative_evidence(
        brief, structured, question="Tại sao không chọn Lean?", ingredient_id=None,
        detail_level="manager", question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    comparisons = [item for item in retrieval.evidence if item["type"] == "STRATEGY_COMPARISON"]
    assert retrieval.intent == "STRATEGY_COMPARISON"
    assert comparisons and {item["right_strategy"] for item in comparisons} == {"lean"}
    assert not any(item.get("right_strategy") == "protected" for item in comparisons)
    assert not any(item["type"] in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY"} for item in retrieval.evidence)


def test_plan_strategy_retrieval_limits_explicit_comparison_to_requested_strategies():
    brief, _facts, _evidence, structured = _structured_facts()
    retrieval = retrieve_narrative_evidence(
        brief, structured, question="Protected khác Balanced thế nào?", ingredient_id=None,
        detail_level="manager", question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    comparisons = [item for item in retrieval.evidence if item["type"] == "STRATEGY_COMPARISON"]
    assert retrieval.intent == "STRATEGY_COMPARISON"
    assert comparisons and {item["right_strategy"] for item in comparisons} == {"protected"}
    assert not any(item.get("right_strategy") == "lean" for item in comparisons)


def test_plan_tradeoff_retrieval_uses_selected_plan_and_comparisons_not_procurement():
    brief, _facts, _evidence, structured = _structured_facts()
    retrieval = retrieve_narrative_evidence(
        brief, structured, question="Trade-off của kế hoạch này là gì?", ingredient_id=None,
        detail_level="manager", question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    types = [item["type"] for item in retrieval.evidence]
    assert retrieval.intent == "PLAN_TRADEOFF"
    assert types[0] == "PLAN_OVERVIEW"
    assert "STRATEGY_COMPARISON" in types
    assert not any(item_type in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY"} for item_type in types)


def test_plan_strategy_retrieval_adds_selected_risk_only_when_the_question_requests_it():
    brief, _facts, _evidence, structured = _structured_facts()
    records = [*structured, {"evidence_id": "selected-risk", "type": "SELECTED_PLAN_RISK_METRICS"}]

    retrieval = retrieve_narrative_evidence(
        brief, records, question="Trade-off và rủi ro của kế hoạch này là gì?", ingredient_id=None,
        detail_level="manager", question_scope=QuestionScope.PLAN_STRATEGY.value,
    )

    assert any(item["type"] == "SELECTED_PLAN_RISK_METRICS" for item in retrieval.evidence)


def test_entity_operational_retrieval_remains_procurement_scoped_without_strategy_leakage():
    brief, _facts, _evidence, structured = _structured_facts()
    records = [
        *structured,
        {"evidence_id": "banana-reason", "type": "PROCUREMENT_REASON", "ingredient_id": "banana", "classification": "CAUSAL"},
        {"evidence_id": "banana-quantity", "type": "PROCUREMENT_QUANTITY", "ingredient_id": "banana"},
        {"evidence_id": "banana-demand", "type": "DEMAND_HORIZON_SUMMARY", "ingredient_id": "banana"},
    ]
    retrieval = retrieve_narrative_evidence(
        brief, records, question="Tại sao phải nhập chuối?", ingredient_id="banana",
        detail_level="simple", question_scope=QuestionScope.ENTITY_OPERATIONAL.value,
    )

    assert retrieval.intent == "WHY_PROCUREMENT"
    assert {item["type"] for item in retrieval.evidence} >= {"PROCUREMENT_REASON", "PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY"}
    assert not any(item["type"].startswith("STRATEGY_") for item in retrieval.evidence)


def test_question_scope_classifies_plan_general_closed_and_entity_questions_deterministically():
    plan_questions = (
        "Tại sao chọn kế hoạch này?",
        "Vì sao đây là phương án được đề xuất?",
        "Tại sao chọn Balanced?",
        "Tại sao không chọn Lean?",
        "Protected khác Balanced thế nào?",
        "Trade-off của kế hoạch này là gì?",
        "Why was this plan selected?",
    )
    for question in plan_questions:
        assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY

    general_questions = (
        "Kế hoạch này có gì đáng chú ý?",
        "Tôi cần chú ý điều gì?",
        "Rủi ro chính của kế hoạch này là gì?",
        "What should I pay attention to in this plan?",
    )
    for question in general_questions:
        assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.GENERAL_DECISION

    closed_questions = ("Tổng chi phí là bao nhiêu?", "Kế hoạch nào được chọn?", "Có vượt ngân sách không?")
    for question in closed_questions:
        assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.CLOSED_FACT

    entity_questions = ("Tại sao phải nhập chuối?", "Vì sao cần mua chuối?", "Nhu cầu chuối tuần tới?", "Why do we need to buy bananas?")
    for question in entity_questions:
        assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.ENTITY_OPERATIONAL
    assert classify_question_scope("Tại sao chọn kế hoạch này?", has_explicit_ingredient_id=True) is QuestionScope.ENTITY_OPERATIONAL


def test_characterizes_fallback_omits_existing_semantic_selection_facts():
    brief, facts, _evidence, _structured = _structured_facts()
    assert any(fact.fact_type == "STRATEGY_SELECTION_PROOF" for fact in facts)
    assert any(fact.fact_type == "STRATEGY_COMPARISON" for fact in facts)

    response = DecisionNarrativeProvider(None, None).explain(
        brief, question="Tại sao chọn kế hoạch này?", language="vi", detail_level="simple", semantic_facts=facts,
    )

    assert response.provider == "shelfcash_decision_intelligence"
    assert all("SEMANTIC_STRATEGY_" not in claim.type for claim in response.claims)
    assert all("semantic_strategy_" not in citation.evidence_id for citation in response.citations)


def test_existing_semantic_strategy_facts_are_persisted_package_derivations():
    _brief_value, facts, _evidence, structured = _structured_facts()
    proof = next(fact for fact in facts if fact.fact_type == "STRATEGY_SELECTION_PROOF")
    comparison = next(fact for fact in facts if fact.fact_type == "STRATEGY_COMPARISON" and fact.entities["right_strategy"] == "protected")

    assert proof.classification is SemanticFactClassification.CAUSAL
    assert proof.values["selected_strategy"] == "balanced"
    assert proof.values["eligible_strategies"] == ["balanced", "protected"]
    assert proof.provenance.source_type == "DecisionRun.package_json"
    assert comparison.values["left_strategy"] == "balanced"
    assert comparison.values["purchase_cost_delta"] == -20.0
    assert comparison.provenance.source_type == "DecisionRun.package_json"
    assert any(item["type"] == "STRATEGY_SELECTION_PROOF" for item in structured)
    assert any(item["type"] == "STRATEGY_COMPARISON" for item in structured)
