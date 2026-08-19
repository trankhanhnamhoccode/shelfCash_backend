from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class RiskBrief(_Contract):
    stockout_probability: float | None = None; expected_fill_rate: float | None = None
    shortage_quantity: float | None = None; waste_quantity: float | None = None


class CriticBrief(_Contract):
    hard_violations: list[str] = Field(default_factory=list); warnings: list[str] = Field(default_factory=list)


class EvidenceBrief(_Contract):
    evidence_id: str; label: str; source_type: str; entities: dict[str, str] = Field(default_factory=dict)


class DecisionBriefFacts(_Contract):
    decision_run_id: str; store_id: str; status: str
    forecast: ForecastBrief; recommendation: RecommendationBrief
    procurement_rows: list[ProcurementRowBrief] = Field(default_factory=list)
    ingredient_demand: list[IngredientDemandBrief] = Field(default_factory=list)
    risk: RiskBrief; critic: CriticBrief
    evidence: list[EvidenceBrief] = Field(default_factory=list)
    data_availability: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime


class ExplanationClaim(_Contract):
    type: str; value: Any = None; unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class Citation(_Contract):
    evidence_id: str; label: str; source_type: str


class DecisionExplanationResponse(_Contract):
    # Legacy fields are retained for existing clients.
    source: str; language: str; detail_level: str; summary: str
    why_this_plan: list[str] = Field(default_factory=list); main_risks: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list); important_assumptions: list[str] = Field(default_factory=list)
    decision_run_id: str; answer: str; intent: str; entities: dict[str, list[str]]
    claims: list[ExplanationClaim] = Field(default_factory=list); citations: list[Citation] = Field(default_factory=list)
    grounded: bool; provider: str
    raw_response: dict[str, Any] | None = None


class WhatIfOrderChange(_Contract):
    ingredient_id: str; baseline_quantity: float | None = None; hypothetical_quantity: float | None = None
    quantity_delta: float | None = None; baseline_supplier_id: str | None = None; hypothetical_supplier_id: str | None = None
    baseline_arrival_date: date | None = None; hypothetical_arrival_date: date | None = None


class WhatIfComparison(_Contract):
    recommendation_changed: bool
    baseline_strategy: str | None = None; hypothetical_strategy: str | None = None
    purchase_cost_delta: float | None = None; expected_fill_rate_delta: float | None = None
    stockout_probability_delta: float | None = None; shortage_quantity_delta: float | None = None; waste_quantity_delta: float | None = None
    order_changes: list[WhatIfOrderChange] = Field(default_factory=list)
    warnings_added: list[str] = Field(default_factory=list); warnings_removed: list[str] = Field(default_factory=list)
    hard_violations_added: list[str] = Field(default_factory=list); hard_violations_removed: list[str] = Field(default_factory=list)


class WhatIfResponse(_Contract):
    decision_run_id: str; baseline: DecisionBriefFacts; hypothetical: DecisionBriefFacts
    mutations: dict[str, object | None]; comparison: WhatIfComparison
    grounded_explanation: dict[str, object] | None = None; generated_at: datetime
