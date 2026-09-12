from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from shelfcash_core.inventory.contracts import InventorySimulationPackage
from shelfcash_core.optimization.contracts import ProcurementDecisionLine


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionRunRequest(_Strict):
    forecast_run_id: str
    as_of_date: date
    horizon_days: int = Field(ge=1, le=90)
    engine_mode: Literal["legacy", "deterministic", "stochastic"] | None = None
    include_open_purchase_orders: bool = True
    budget_override: int | None = Field(default=None, ge=0)
    scenario_count: int | None = Field(default=None, ge=1, le=1000)
    random_seed: int | None = None


class ExplanationRequest(_Strict):
    language: Literal["vi", "en"] = "vi"
    detail_level: Literal["simple", "manager", "technical"] = "simple"
    question: str | None = Field(default=None, max_length=2000)
    # Optional so existing question-only explanation requests remain valid.
    ingredient_id: str | None = Field(default=None, min_length=1, max_length=255)


class WhatIfRequest(_Strict):
    demand_multiplier: float | None = Field(default=None, gt=0)
    supplier_delay_days: int | None = Field(default=None, ge=0)
    budget_limit: int | None = Field(default=None, ge=0)
    strategy: Literal["lean", "balanced", "protected"] | None = None


class _DecisionPackageModel(BaseModel):
    """Public raw-package boundary; nested diagnostics remain bounded JSON."""

    model_config = ConfigDict(extra="forbid")


class DecisionPackagePlan(_DecisionPackageModel):
    items: list[ProcurementDecisionLine]


class DecisionPackageCritic(_DecisionPackageModel):
    status: Literal["pass", "fail"]
    findings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionPackageStrategy(_DecisionPackageModel):
    strategy: Literal["lean", "balanced", "protected"]
    is_feasible: bool
    purchase_cost: float | None = None
    expected_recourse_cost: float | None = None
    business_metrics: dict[str, Any] = Field(default_factory=dict)
    items: list[ProcurementDecisionLine] = Field(default_factory=list)
    critic: DecisionPackageCritic
    stress_tests: InventorySimulationPackage | None = None
    technical_metrics: dict[str, Any] = Field(default_factory=dict)


class DecisionPackageSelection(_DecisionPackageModel):
    rule: str | None = None
    selected_strategy: Literal["lean", "balanced", "protected"] | None = None
    eligible_candidates: list[str] = Field(default_factory=list)


class DecisionPackageReasonCode(_DecisionPackageModel):
    code: str
    entity_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class DecisionPackageTechnicalMetrics(_DecisionPackageModel):
    scenario_count: int
    scenario_method: str
    random_seed: int
    optimizer_type: str
    cvar_alpha: float | None = None
    core_version: str
    stochastic_saa_enabled: bool
    risk_evaluation_status: str | None = None
    risk_evaluation_sample_count: int | None = None
    baseline_engine: str
    scenario_diagnostics: dict[str, Any] = Field(default_factory=dict)
    forecast_trace: dict[str, Any] = Field(default_factory=dict)
    shortage_economics: dict[str, Any] = Field(default_factory=dict)


class DecisionPackageAssistant(_DecisionPackageModel):
    overall_summary: dict[str, Any] | None = None
    ingredient_synthesis: list[dict[str, Any]] | None = None
    ingredient_synthesis_diagnostics: dict[str, Any] | None = None


class DecisionPackage(_DecisionPackageModel):
    decision_run_id: str
    store_id: str
    as_of_date: date
    horizon_days: int
    status: Literal["completed", "completed_with_no_feasible_recommendation"]
    engine_mode: Literal["deterministic", "stochastic"]
    recommended_strategy: Literal["lean", "balanced", "protected"] | None = None
    business_metrics: dict[str, Any]
    recommended_plan: DecisionPackagePlan
    ingredient_demand: list[dict[str, Any]]
    inventory_risk: InventorySimulationPackage
    strategies: dict[str, DecisionPackageStrategy]
    strategy_selection: DecisionPackageSelection
    stress_tests: InventorySimulationPackage | list[InventorySimulationPackage]
    critic: DecisionPackageCritic
    reason_codes: list[DecisionPackageReasonCode]
    warnings: list[str]
    technical_metrics: DecisionPackageTechnicalMetrics
    assistant: DecisionPackageAssistant | None = None
