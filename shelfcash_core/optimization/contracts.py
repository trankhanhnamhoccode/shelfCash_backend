from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shelfcash_core.bom.contracts import UnitConversionRule
from shelfcash_core.inventory.contracts import (
    ConsequenceCostAssumption,
    InboundDelivery,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPackage,
    InventorySimulationPolicy,
)
from shelfcash_core.inventory.stress import StressScenarioDefinition

StrategyName = Literal["LEAN", "BALANCED", "PROTECTED"]


class StrictOptimizationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SupplierOffer(StrictOptimizationContract):
    offer_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    order_date: date
    pack_size: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    delivery_cost: float = Field(default=0, ge=0)
    minimum_order_quantity: float = Field(default=0, ge=0)
    maximum_order_quantity: float | None = Field(default=None, gt=0)
    lead_time_days: int = Field(ge=0)
    available_delivery_days: list[int] | None = Field(
        default=None,
        description="Optional allowed weekday numbers (Monday=0); null means unrestricted.",
    )
    shelf_life_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Calendar-day offset from arrival_date to the inclusive expiry date; "
            "zero means usable on arrival_date only."
        ),
    )
    expiry_tracking_mode: Literal["required", "not_required", "unknown"] = "unknown"
    available: bool = True
    order_cutoff_date: date | None = None
    emergency: bool = False

    @model_validator(mode="after")
    def validate_quantity_bounds(self) -> SupplierOffer:
        if (
            self.maximum_order_quantity is not None
            and self.maximum_order_quantity < self.minimum_order_quantity
        ):
            raise ValueError("maximum_order_quantity cannot be below MOQ.")
        if self.available_delivery_days is not None and any(
            day < 0 or day > 6 for day in self.available_delivery_days
        ):
            raise ValueError("available_delivery_days must contain weekday values 0..6.")
        return self


class SupplierConstraint(StrictOptimizationContract):
    supplier_id: str = Field(min_length=1)
    store_id: str | None = None
    ingredient_id: str | None = None
    unit: str | None = None
    maximum_total_quantity: float | None = Field(default=None, ge=0)
    maximum_total_cost: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_quantity_scope(self) -> SupplierConstraint:
        if self.maximum_total_quantity is not None and (
            self.ingredient_id is None or self.unit is None
        ):
            raise ValueError(
                "A supplier quantity cap requires ingredient_id and unit."
            )
        return self


class ProcurementDecisionLine(StrictOptimizationContract):
    offer_id: str
    supplier_id: str
    store_id: str
    ingredient_id: str
    unit: str
    order_date: date
    arrival_date: date
    pack_count: int = Field(ge=0)
    pack_size: float = Field(gt=0)
    order_quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)
    purchase_cost: float = Field(ge=0)
    delivery_cost: float = Field(ge=0)
    shelf_life_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Calendar-day offset from arrival_date to the inclusive expiry date; "
            "zero means usable on arrival_date only."
        ),
    )
    projected_expiry_date: date | None = None
    expiry_tracking_mode: Literal["required", "not_required", "unknown"] = "unknown"
    emergency: bool = False

    @model_validator(mode="after")
    def validate_derived_values(self) -> ProcurementDecisionLine:
        expected_quantity = self.pack_count * self.pack_size
        if not math.isclose(
            self.order_quantity, expected_quantity, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("order_quantity must equal pack_count * pack_size.")
        expected_cost = self.order_quantity * self.unit_price
        if not math.isclose(
            self.purchase_cost, expected_cost, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("purchase_cost must equal order_quantity * unit_price.")
        return self


class StrategyProfile(StrictOptimizationContract):
    name: StrategyName
    # These are dimensionless preference multipliers.  The monetary base
    # costs live in ConsequenceCostAssumption and must use the same currency
    # / cost unit as SupplierOffer.unit_price.
    shortage_penalty: float = Field(ge=0, description="Dimensionless shortage-cost multiplier.")
    holding_penalty: float = Field(ge=0, description="Dimensionless holding-cost multiplier.")
    waste_penalty: float = Field(ge=0, description="Dimensionless waste-cost multiplier.")
    cash_penalty: float = Field(ge=0)
    cvar_weight: float = Field(default=0, ge=0)
    cvar_alpha: float = Field(default=0.95, gt=0, lt=1)
    maximum_stockout_probability: float | None = Field(default=None, ge=0, le=1)
    minimum_expected_fill_rate: float | None = Field(default=None, ge=0, le=1)
    minimum_fill_rate: float | None = Field(default=None, ge=0, le=1)
    required_fill_rate_probability: float | None = Field(default=None, ge=0, le=1)
    # Universal exact-simulation safety floors.  These apply even when the
    # candidate MILP does not carry an equivalent service constraint.
    minimum_acceptable_fill_rate: float = Field(default=0.5, ge=0, le=1)
    maximum_acceptable_stockout_probability: float = Field(
        default=0.5, ge=0, le=1
    )
    maximum_fill_rate_model_gap: float = Field(default=0.05, ge=0, le=1)
    maximum_stockout_probability_model_gap: float = Field(
        default=0.05, ge=0, le=1
    )


class ProcurementPlan(StrictOptimizationContract):
    plan_id: str = Field(min_length=1)
    strategy: StrategyName
    orders: list[ProcurementDecisionLine]
    scenario_recourse_orders: dict[str, list[ProcurementDecisionLine]] = Field(
        default_factory=dict
    )
    purchase_cost: float = Field(ge=0)
    expected_recourse_cost: float = Field(default=0, ge=0)
    objective_value: float | None = None
    solver_status: str
    completed: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CriticResult(StrictOptimizationContract):
    passed: bool
    hard_violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateEvaluation(StrictOptimizationContract):
    plan: ProcurementPlan
    simulation: InventorySimulationPackage | None = None
    # A fixed-plan, probability-weighted Exact FEFO evaluation.  It is kept
    # separate from the deterministic design simulation so it can never alter
    # the optimization model or the strategy selector.
    risk_simulation: InventorySimulationPackage | None = None
    stress_simulation: InventorySimulationPackage | None = None
    critic: CriticResult


class OptimizationRequest(StrictOptimizationContract):
    request_id: str = Field(min_length=1)
    decision_date: date
    planning_end_date: date
    initial_inventory: list[InventoryLot]
    demand_scenarios: list[InventoryDemandScenario]
    supplier_offers: list[SupplierOffer]
    existing_inbound: list[InboundDelivery] = Field(default_factory=list)
    supplier_constraints: list[SupplierConstraint] = Field(default_factory=list)
    budget: float | None = Field(default=None, ge=0)
    cost_assumptions: list[ConsequenceCostAssumption] = Field(default_factory=list)
    unit_conversions: list[UnitConversionRule] = Field(default_factory=list)
    inventory_policy: InventorySimulationPolicy = Field(
        default_factory=InventorySimulationPolicy
    )
    stress_scenarios: list[StressScenarioDefinition] = Field(default_factory=list)
    stress_base_scenario_id: str | None = None
    strategy_profiles: list[StrategyProfile] = Field(default_factory=list)
    unknown_constraints: list[str] = Field(default_factory=list)
    # Canonical, normalized capacity configuration and evaluation context.
    # It is descriptive for constraints that cannot be represented in the
    # ingredient-unit MILP (for example warehouse volume without conversion
    # metadata), while ingredient maximum-stock limits live in
    # ``cost_assumptions.capacity_quantity``.
    capacity_context: dict[str, Any] = Field(default_factory=dict)
    # These scenarios are evaluation-only.  ``demand_scenarios`` remains the
    # deterministic optimizer design set.
    risk_demand_scenarios: list[InventoryDemandScenario] = Field(default_factory=list)
    risk_evaluation_metadata: dict[str, Any] = Field(default_factory=dict)
    stochastic: bool = True
    seed: int = 0

    @model_validator(mode="after")
    def validate_request(self) -> OptimizationRequest:
        if self.planning_end_date < self.decision_date:
            raise ValueError("planning_end_date cannot precede decision_date.")
        scenario_ids = [scenario.scenario_id for scenario in self.demand_scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("Demand scenario identifiers must be unique.")
        for scenario in self.demand_scenarios:
            if any(
                line.target_date < self.decision_date
                or line.target_date > self.planning_end_date
                for line in scenario.lines
            ):
                raise ValueError(
                    "HORIZON_MISMATCH: demand lines must fall inside the planning "
                    "horizon."
                )
            if (
                scenario.simulation_start_date is not None
                and scenario.simulation_start_date > self.decision_date
            ) or (
                scenario.simulation_end_date is not None
                and scenario.simulation_end_date < self.planning_end_date
            ):
                raise ValueError(
                    "HORIZON_MISMATCH: scenario simulation window does not cover "
                    "the optimization planning horizon."
                )
        weights = [scenario.probability_weight for scenario in self.demand_scenarios]
        if any(weight is not None for weight in weights) and not all(
            weight is not None for weight in weights
        ):
            raise ValueError("Demand probability weights cannot be partially missing.")
        if weights and all(weight is not None for weight in weights):
            total = sum(float(weight) for weight in weights if weight is not None)
            if not math.isclose(total, 1, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Probabilistic demand weights must sum to one.")
        risk_ids = [scenario.scenario_id for scenario in self.risk_demand_scenarios]
        if len(risk_ids) != len(set(risk_ids)):
            raise ValueError("Risk demand scenario identifiers must be unique.")
        risk_weights = [scenario.probability_weight for scenario in self.risk_demand_scenarios]
        if risk_weights and not all(weight is not None for weight in risk_weights):
            raise ValueError("Risk demand scenarios require explicit probability weights.")
        if risk_weights:
            total = sum(float(weight) for weight in risk_weights if weight is not None)
            if not math.isclose(total, 1, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Risk demand probability weights must sum to one.")
        return self


class OptimizationResult(StrictOptimizationContract):
    request_id: str
    evaluations: dict[StrategyName, CandidateEvaluation]
    recommended_strategy: StrategyName | None = None
    status: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RobustOptimizationStatus(StrictOptimizationContract):
    status: Literal["AVAILABLE", "NOT_AVAILABLE"]
    method: str
    missing_prerequisites: list[str] = Field(default_factory=list)
    guarantee: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RollingHorizonStep(StrictOptimizationContract):
    decision_date: date
    optimization_result: OptimizationResult
    executed_orders: list[ProcurementDecisionLine]


class RollingHorizonResult(StrictOptimizationContract):
    steps: list[RollingHorizonStep]
    provenance: dict[str, Any] = Field(default_factory=dict)
