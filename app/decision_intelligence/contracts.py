from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.decision import WhatIfRequest


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastBrief(_Contract):
    forecast_run_id: str | None = None; model_version: str | None = None
    horizon_days: int | None = None; cutoff_date: date | None = None


class RecommendationBrief(_Contract):
    available: bool; strategy: Literal["lean", "balanced", "protected"] | None = None
    summary: str | None = None; total_purchase_cost: float | None = None
    expected_fill_rate: float | None = None


class ProcurementRowBrief(_Contract):
    ingredient_id: str; ingredient_name: str | None = None; supplier_id: str | None = None
    supplier_name: str | None = None; quantity: float; unit: str | None = None
    pack_count: int | None = None; pack_size: float | None = None; order_date: date | None = None
    arrival_date: date | None = None; purchase_cost: float | None = None
    reason_codes: list[str] = Field(default_factory=list)


class IngredientDemandBrief(_Contract):
    ingredient_id: str; ingredient_name: str | None = None; unit: str | None = None
    target_date: date | str
    p25: float | None = None; p50: float | None = None; p75: float | None = None
    contributions: list[dict[str, Any]] = Field(default_factory=list)


class IngredientDemandSummaryBrief(_Contract):
    """Horizon projection of daily demand; never replaces daily rows."""

    ingredient_id: str; ingredient_name: str | None = None; unit: str | None = None
    period_start: date | str; period_end: date | str
    p25_total: float; p50_total: float; p75_total: float
    daily_p50_min: float; daily_p50_max: float
    peak_date: date | str; peak_p50: float
    aggregation_method: Literal["sum_daily_quantiles"]


class AssistantSummary(_Contract):
    """Read-only overall Decision Assistant text retained with a Decision Run."""

    headline: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    warning_summary: str | None = None
    source: Literal["llm", "deterministic_fallback"]
    grounded: bool
    # Exact model output is retained separately from the validated summary.
    raw_response: dict[str, Any] | str | None = None
    # Transport, parsing and validation outcome. This intentionally remains
    # public so callers can distinguish a real Qwen answer from a fallback.
    llm_diagnostics: dict[str, Any] | None = None


class RiskBrief(_Contract):
    stockout_probability: float | None = None; expected_fill_rate: float | None = None
    shortage_quantity: float | None = None; waste_quantity: float | None = None


class RiskDetail(_Contract):
    """Deterministic, human-facing projection of a risk/limitation code."""

    code: str
    classification: Literal["risk", "limitation", "unknown"]
    category: str
    severity: Literal["info", "warning", "critical"]
    title: str
    meaning: str | None = None
    recommended_action: str | None = None
    scope: Literal["run", "ingredient", "supplier", "strategy"]
    ingredient_id: str | None = None
    ingredient_name: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_count: int = Field(ge=1)


class PresentedWarning(_Contract):
    """Deterministic manager-facing projection of a raw diagnostic code."""

    code: str
    severity: Literal["info", "warning", "critical"]
    audience: Literal["user", "technical"]
    title: str
    message: str


class IngredientSynthesis(_Contract):
    """Read-only, manager-facing synthesis for one Decision Run ingredient."""

    ingredient_id: str
    ingredient_name: str | None = None
    unit: str | None = None
    importance: Literal["normal", "watch", "critical"]
    source: Literal["rule_based", "llm", "deterministic_fallback"]
    headline: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class StrategyMetricsBrief(_Contract):
    """Comparable candidate metrics with their persisted evaluation semantics."""

    purchase_cost: float | None = None
    expected_fill_rate: float | None = None
    stockout_probability: float | None = None
    risk_evaluation_status: str | None = None
    risk_evaluation_method: str | None = None


class StrategyCriticBrief(_Contract):
    hard_violation_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    stress_shortage_observed: bool | None = None
    stress_capacity_violation: bool | None = None


class StrategyDeltaBrief(_Contract):
    """Directional deltas: selected strategy minus this candidate."""

    left_strategy: str
    right_strategy: str
    purchase_cost_delta: float | None = None
    expected_fill_rate_delta: float | None = None
    expected_fill_rate_percentage_point_delta: float | None = None
    stockout_probability_delta: float | None = None


class StrategyCandidateBrief(_Contract):
    strategy: str
    label: str
    selected: bool
    feasible: bool
    metrics: StrategyMetricsBrief
    critic: StrategyCriticBrief
    vs_selected: StrategyDeltaBrief | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class StrategySelectionReasonBrief(_Contract):
    """Machine-safe selection proof; absent proof is intentionally explicit."""

    available: bool
    selected_strategy: str | None = None
    rule: str | None = None
    eligible_strategies: list[str] = Field(default_factory=list)
    selection_metric: str | None = None
    tie_breaker: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class StrategyComparisonBrief(_Contract):
    selected_strategy: str | None = None
    candidates: list[StrategyCandidateBrief] = Field(default_factory=list)
    selection_reason: StrategySelectionReasonBrief


class CriticBrief(_Contract):
    hard_violations: list[str] = Field(default_factory=list); warnings: list[str] = Field(default_factory=list)


class EvidenceBrief(_Contract):
    evidence_id: str; label: str; source_type: str; entities: dict[str, str] = Field(default_factory=dict)


class DecisionBriefFacts(_Contract):
    decision_run_id: str; store_id: str; status: str
    forecast: ForecastBrief; recommendation: RecommendationBrief
    procurement_rows: list[ProcurementRowBrief] = Field(default_factory=list)
    ingredient_demand: list[IngredientDemandBrief] = Field(default_factory=list)
    ingredient_demand_summary: list[IngredientDemandSummaryBrief] = Field(default_factory=list)
    risk: RiskBrief; critic: CriticBrief
    risk_details: list[RiskDetail] = Field(default_factory=list)
    # Additive presentation fields. Raw warnings and risk_details remain
    # available for diagnostics and existing consumers.
    ingredient_synthesis: list[IngredientSynthesis] = Field(default_factory=list)
    presented_warnings: list[PresentedWarning] = Field(default_factory=list)
    strategy_comparison: StrategyComparisonBrief | None = None
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    data_availability: dict[str, str] = Field(default_factory=dict)
    assistant_summary: AssistantSummary | None = None
    generated_at: datetime


class ExplanationClaim(_Contract):
    type: str; value: Any = None; unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Citation(_Contract):
    evidence_id: str; label: str; source_type: str


class DecisionNarrativeClaim(_Contract):
    type: str
    text: str
    evidence_ids: list[str]


class DecisionNarrativeLLMResponse(_Contract):
    """The strict, provider-facing contract before grounding validation."""

    answer: str
    claims: list[DecisionNarrativeClaim]
    used_evidence_ids: list[str]


class DecisionOverallSummaryLLMResponse(_Contract):
    """Strict internal schema for an overall grounded assistant summary."""

    headline: DecisionNarrativeClaim
    summary: DecisionNarrativeClaim
    key_points: list[DecisionNarrativeClaim] = Field(default_factory=list)
    warning_summary: DecisionNarrativeClaim | None = None
    used_evidence_ids: list[str]


class IngredientSynthesisLLMItem(_Contract):
    ingredient_id: str
    headline: str
    summary: str
    claims: list[DecisionNarrativeClaim]
    used_evidence_ids: list[str]


class IngredientSynthesisLLMResponse(_Contract):
    items: list[IngredientSynthesisLLMItem]


class DecisionExplanationResponse(_Contract):
    # Legacy fields are retained for existing clients.
    source: str; language: str; detail_level: str; summary: str
    why_this_plan: list[str] = Field(default_factory=list); main_risks: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list); important_assumptions: list[str] = Field(default_factory=list)
    decision_run_id: str; answer: str; intent: str; entities: dict[str, list[str]]
    claims: list[ExplanationClaim] = Field(default_factory=list); citations: list[Citation] = Field(default_factory=list)
    grounded: bool; provider: str
    authority: Literal["HYPOTHETICAL"] | None = None
    raw_response: dict[str, Any] | str | None = None
    llm_diagnostics: dict[str, Any] | None = None


class WhatIfOrderChange(_Contract):
    ingredient_id: str; baseline_quantity: float | None = None; hypothetical_quantity: float | None = None
    quantity_delta: float | None = None; baseline_supplier_id: str | None = None; hypothetical_supplier_id: str | None = None
    baseline_arrival_date: date | None = None; hypothetical_arrival_date: date | None = None
    ingredient_name: str | None = None; unit: str | None = None
    change_type: Literal["added", "removed", "increased", "decreased"] | None = None


class WhatIfMutationFacts(_Contract):
    demand_multiplier: float
    demand_change_ratio: float
    demand_change_percent: float = Field(
        description="Relative demand change from multiplier 1.0, expressed as percent; 20 means +20%.",
    )
    # Kept while no external consumer migration is required.  This value was
    # never percentage points; use demand_change_percent for new consumers.
    demand_change_percentage_points: float | None = Field(default=None, deprecated=True)
    supplier_delay_days: int
    budget_limit: int | None = None
    strategy_override: str | None = None


class WhatIfStrategyChange(_Contract):
    changed: bool
    baseline_strategy: str | None = None
    hypothetical_strategy: str | None = None
    forced_by_request: bool = False


class WhatIfRiskChange(_Contract):
    code: str
    classification: Literal["risk", "limitation", "unknown"]
    scope: Literal["run", "ingredient", "supplier", "strategy"]
    ingredient_id: str | None = None


class WhatIfComparison(_Contract):
    recommendation_changed: bool
    baseline_strategy: str | None = None; hypothetical_strategy: str | None = None
    purchase_cost_delta: float | None = None; expected_fill_rate_delta: float | None = None
    stockout_probability_delta: float | None = None; shortage_quantity_delta: float | None = None; waste_quantity_delta: float | None = None
    expected_fill_rate_percentage_point_delta: float | None = None
    baseline_recommendation_available: bool | None = None
    hypothetical_recommendation_available: bool | None = None
    feasibility_changed: bool = False
    strategy_change: WhatIfStrategyChange | None = None
    order_changes: list[WhatIfOrderChange] = Field(default_factory=list)
    warnings_added: list[str] = Field(default_factory=list); warnings_removed: list[str] = Field(default_factory=list)
    hard_violations_added: list[str] = Field(default_factory=list); hard_violations_removed: list[str] = Field(default_factory=list)
    new_issues: list[WhatIfRiskChange] = Field(default_factory=list)
    resolved_issues: list[WhatIfRiskChange] = Field(default_factory=list)
    # Compatibility aliases: these lists can include LIMITATION entries, so
    # new consumers must use the semantically correct *_issues fields.
    new_risks: list[WhatIfRiskChange] = Field(default_factory=list, deprecated=True)
    resolved_risks: list[WhatIfRiskChange] = Field(default_factory=list, deprecated=True)


class WhatIfResponse(_Contract):
    decision_run_id: str; baseline: DecisionBriefFacts; hypothetical: DecisionBriefFacts
    mutations: WhatIfRequest; comparison: WhatIfComparison
    mutation_facts: WhatIfMutationFacts | None = None
    grounded_explanation: DecisionExplanationResponse | None = None; generated_at: datetime
