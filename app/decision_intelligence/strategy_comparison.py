"""Read-only projection of canonical strategy semantic facts."""
from __future__ import annotations

from app.decision_intelligence.contracts import (
    DecisionBriefFacts,
    StrategyCandidateBrief,
    StrategyComparisonBrief,
    StrategyCriticBrief,
    StrategyDeltaBrief,
    StrategyMetricsBrief,
    StrategySelectionReasonBrief,
)
from app.decision_intelligence.semantic_evidence import SemanticFact, SemanticFactClassification


STRATEGY_LABELS = {
    "lean": "Tiết kiệm",
    "balanced": "Cân bằng",
    "protected": "An toàn",
}
_STRATEGY_ORDER = {"lean": 0, "balanced": 1, "protected": 2}


def strategy_label(strategy: str) -> str:
    return STRATEGY_LABELS.get(strategy, strategy)


def project_strategy_comparison(
    brief: DecisionBriefFacts,
    facts: list[SemanticFact],
) -> StrategyComparisonBrief | None:
    candidates = [fact for fact in facts if fact.fact_type == "STRATEGY_CANDIDATE_METRICS"]
    if not candidates:
        return None
    deltas = {
        (fact.entities.get("left_strategy"), fact.entities.get("right_strategy")): fact
        for fact in facts if fact.fact_type == "STRATEGY_COMPARISON"
    }
    proof = next(
        (fact for fact in facts
         if fact.fact_type == "STRATEGY_SELECTION_PROOF"
         and fact.classification is SemanticFactClassification.CAUSAL),
        None,
    )
    selected = brief.recommendation.strategy
    projected: list[StrategyCandidateBrief] = []
    for fact in sorted(candidates, key=lambda item: (_STRATEGY_ORDER.get(item.entities["strategy"], 99), item.entities["strategy"])):
        values = fact.values
        strategy = fact.entities["strategy"]
        delta = deltas.get((selected, strategy)) if selected and strategy != selected else None
        projected.append(StrategyCandidateBrief(
            strategy=strategy,
            label=strategy_label(strategy),
            selected=bool(values.get("selected")),
            feasible=bool(values.get("feasible")),
            metrics=StrategyMetricsBrief(
                purchase_cost=values.get("purchase_cost"),
                expected_fill_rate=values.get("expected_fill_rate"),
                stockout_probability=values.get("stockout_probability"),
                risk_evaluation_status=values.get("risk_evaluation_status"),
                risk_evaluation_method=values.get("risk_evaluation_method"),
            ),
            critic=StrategyCriticBrief(
                hard_violation_count=int(values.get("hard_violation_count") or 0),
                warning_count=int(values.get("warning_count") or 0),
                stress_shortage_observed=values.get("stress_shortage_observed"),
                stress_capacity_violation=values.get("stress_capacity_violation"),
            ),
            vs_selected=_delta(delta) if delta is not None else None,
            evidence_ids=fact.source_evidence_ids,
        ))
    return StrategyComparisonBrief(
        selected_strategy=selected,
        candidates=projected,
        selection_reason=_selection_reason(proof),
    )


def _delta(fact: SemanticFact) -> StrategyDeltaBrief:
    values = fact.values
    return StrategyDeltaBrief(
        left_strategy=str(values["left_strategy"]),
        right_strategy=str(values["right_strategy"]),
        purchase_cost_delta=values.get("purchase_cost_delta"),
        expected_fill_rate_delta=values.get("expected_fill_rate_delta"),
        expected_fill_rate_percentage_point_delta=values.get("expected_fill_rate_percentage_point_delta"),
        stockout_probability_delta=values.get("stockout_probability_delta"),
    )


def _selection_reason(fact: SemanticFact | None) -> StrategySelectionReasonBrief:
    if fact is None:
        return StrategySelectionReasonBrief(available=False)
    values = fact.values
    return StrategySelectionReasonBrief(
        available=True,
        selected_strategy=str(values["selected_strategy"]),
        rule=str(values["rule"]),
        eligible_strategies=[str(value) for value in values["eligible_strategies"]],
        selection_metric=str(values["selection_metric"]),
        tie_breaker=str(values["tie_breaker"]),
        evidence_ids=fact.source_evidence_ids,
    )
