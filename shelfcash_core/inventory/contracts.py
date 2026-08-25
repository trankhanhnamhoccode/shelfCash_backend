from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInventoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class InventoryLot(StrictInventoryContract):
    lot_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    quantity_remaining: float = Field(ge=0)
    unit: str = Field(min_length=1)
    received_date: date | None = None
    expiry_date: date | None = None
    unit_cost: float | None = Field(default=None, ge=0)
    location: str | None = None
    supplier_id: str | None = None
    source_type: Literal["initial_inventory", "inbound", "planned_inbound"] = (
        "initial_inventory"
    )
    source_reference_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

class InboundDelivery(StrictInventoryContract):
    delivery_id: str = Field(min_length=1)
    lot_id: str = Field(min_length=1)
    purchase_order_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1)
    arrival_date: date
    expiry_date: date | None = None
    unit_cost: float | None = Field(default=None, ge=0)
    location: str | None = None
    arrival_condition: Literal["normal", "arrived_expired_realization"] = "normal"
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_arrived_expired_semantics(self) -> InboundDelivery:
        arrived_expired = (
            self.expiry_date is not None and self.expiry_date < self.arrival_date
        )
        if arrived_expired and self.arrival_condition != "arrived_expired_realization":
            raise ValueError(
                "INVALID_INBOUND_EXPIRY: expiry_date precedes arrival_date."
            )
        if arrived_expired and not self.provenance.get("realization_type"):
            raise ValueError(
                "ARRIVED_EXPIRED requires provenance.realization_type."
            )
        if (
            self.arrival_condition == "arrived_expired_realization"
            and not arrived_expired
        ):
            raise ValueError(
                "arrived_expired_realization requires expiry_date < arrival_date."
            )
        return self

class PlannedInboundDelivery(InboundDelivery):
    """A supplied action already converted into a realized inbound consequence."""

    source_plan_id: str = Field(min_length=1)


class WasteEvent(StrictInventoryContract):
    event_id: str = Field(min_length=1)
    event_date: date
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1)
    lot_id: str | None = None
    reason: str = Field(min_length=1)


class InventoryDemandLine(StrictInventoryContract):
    scenario_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    target_date: date
    quantity: float = Field(ge=0)
    unit: str = Field(min_length=1)


class InventoryDemandScenario(StrictInventoryContract):
    scenario_id: str = Field(min_length=1)
    probability_weight: float | None = Field(default=None, ge=0)
    simulation_start_date: date | None = None
    simulation_end_date: date | None = None
    lines: list[InventoryDemandLine]
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lines(self) -> InventoryDemandScenario:
        keys: set[tuple[str, str, date]] = set()
        for line in self.lines:
            if line.scenario_id != self.scenario_id:
                raise ValueError("Demand line scenario_id không khớp container.")
            key = (line.store_id, line.ingredient_id, line.target_date)
            if key in keys:
                raise ValueError(f"SCENARIO_DUPLICATE_KEY: {key!r}")
            keys.add(key)
        if (
            self.simulation_start_date is not None
            and self.simulation_end_date is not None
            and self.simulation_end_date < self.simulation_start_date
        ):
            raise ValueError("simulation_end_date cannot precede simulation_start_date.")
        if self.lines and self.simulation_start_date is not None and any(
            line.target_date < self.simulation_start_date for line in self.lines
        ):
            raise ValueError("HORIZON_MISMATCH: demand precedes simulation start.")
        if self.lines and self.simulation_end_date is not None and any(
            line.target_date > self.simulation_end_date for line in self.lines
        ):
            raise ValueError("HORIZON_MISMATCH: demand exceeds simulation end.")
        return self


class InventorySimulationPolicy(StrictInventoryContract):
    expiry_inclusive: bool = True
    unknown_expiry: Literal["reject", "warn_and_place_last"] = "reject"
    accounting_tolerance: float = Field(default=1e-8, gt=0)
    at_risk_expiry_days: int = Field(default=2, ge=0)
    waste_threshold: float = Field(default=0, ge=0)
    fill_rate_target: float = Field(default=0.95, ge=0, le=1)
    trace_retention: Literal["full", "summary", "selected"] = "full"
    trace_scenario_ids: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_trace_selection(self) -> InventorySimulationPolicy:
        if self.trace_retention == "selected" and not self.trace_scenario_ids:
            raise ValueError("selected trace retention requires trace_scenario_ids.")
        return self


class ConsequenceCostAssumption(StrictInventoryContract):
    store_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    holding_cost_per_unit_day: float = Field(default=0, ge=0)
    shortage_cost_per_unit: float = Field(default=0, ge=0)
    expired_cost_per_unit: float = Field(default=0, ge=0)
    waste_cost_per_unit: float = Field(default=0, ge=0)
    capacity_quantity: float | None = Field(default=None, ge=0)


class LotConsumptionTrace(StrictInventoryContract):
    scenario_id: str
    simulation_date: date
    store_id: str
    ingredient_id: str
    lot_id: str
    quantity: float = Field(gt=0)
    unit: str
    lot_expiry_date: date | None = None


class LotWasteTrace(StrictInventoryContract):
    scenario_id: str
    simulation_date: date
    store_id: str
    ingredient_id: str
    lot_id: str
    event_id: str
    quantity: float = Field(gt=0)
    unit: str


class LotExpiryTrace(StrictInventoryContract):
    scenario_id: str
    simulation_date: date
    store_id: str
    ingredient_id: str
    lot_id: str
    unit: str
    expiry_date: date
    expired_quantity: float = Field(gt=0)
    source_type: Literal["initial_inventory", "inbound", "planned_inbound"]
    source_reference_id: str | None = None
    supplier_id: str | None = None
    received_date: date | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class FEFOResult(StrictInventoryContract):
    updated_lots: list[InventoryLot]
    fulfilled_quantity: float = Field(ge=0)
    shortage_quantity: float = Field(ge=0)
    traces: list[LotConsumptionTrace]
    warnings: list[str] = Field(default_factory=list)


class DailyInventoryLedger(StrictInventoryContract):
    scenario_id: str
    simulation_date: date
    store_id: str
    ingredient_id: str
    unit: str
    beginning_quantity: float = Field(ge=0)
    inbound_quantity: float = Field(ge=0)
    demand_quantity: float = Field(ge=0)
    fulfilled_quantity: float = Field(ge=0)
    shortage_quantity: float = Field(ge=0)
    expired_quantity: float = Field(ge=0)
    waste_quantity: float = Field(ge=0)
    ending_quantity: float = Field(ge=0)
    maximum_quantity: float = Field(ge=0)
    at_risk_expiry_quantity: float = Field(ge=0)
    capacity_violation_quantity: float = Field(ge=0)
    holding_cost: float | None = Field(default=None, ge=0)
    shortage_cost: float | None = Field(default=None, ge=0)
    expiry_cost: float | None = Field(default=None, ge=0)
    waste_cost: float | None = Field(default=None, ge=0)


class EndingLotState(InventoryLot):
    pass


class InventoryKeySummary(StrictInventoryContract):
    store_id: str
    ingredient_id: str
    unit: str
    total_demand: float = Field(ge=0)
    fulfilled_quantity: float = Field(ge=0)
    shortage_quantity: float = Field(ge=0)
    expired_quantity: float = Field(ge=0)
    explicit_waste_quantity: float = Field(ge=0)
    beginning_inventory: float = Field(ge=0)
    total_inbound: float = Field(ge=0)
    ending_inventory: float = Field(ge=0)
    maximum_inventory: float = Field(ge=0)
    fill_rate: float = Field(ge=0, le=1)
    days_of_supply: float | None = Field(default=None, ge=0)
    at_risk_expiry_quantity: float = Field(ge=0)
    projected_stockout_date: date | None = None
    stockout_event_count: int = Field(ge=0)
    capacity_violation_quantity: float | None = Field(default=None, ge=0)
    holding_cost: float | None = Field(default=None, ge=0)
    shortage_cost: float | None = Field(default=None, ge=0)
    expiry_cost: float | None = Field(default=None, ge=0)
    waste_cost: float | None = Field(default=None, ge=0)
    total_consequence_cost: float | None = Field(default=None, ge=0)


class InventorySimulationSummary(StrictInventoryContract):
    by_key: list[InventoryKeySummary]
    inventory_key_count: int = Field(ge=1)
    number_of_stockout_events: int = Field(ge=0)
    number_of_ingredient_keys_with_stockout: int = Field(ge=0)
    mean_key_fill_rate: float = Field(ge=0, le=1)
    number_of_capacity_violations: int | None = Field(default=None, ge=0)
    # Deprecated compatibility scalars: available only for a single inventory key.
    total_demand: float | None = Field(default=None, ge=0)
    total_fulfilled: float | None = Field(default=None, ge=0)
    total_shortage: float | None = Field(default=None, ge=0)
    total_expired: float | None = Field(default=None, ge=0)
    total_waste: float | None = Field(default=None, ge=0)
    ending_inventory: float | None = Field(default=None, ge=0)
    maximum_inventory: float | None = Field(default=None, ge=0)
    at_risk_expiry_quantity: float | None = Field(default=None, ge=0)
    fill_rate: float | None = Field(default=None, ge=0, le=1)
    capacity_violation_quantity: float | None = Field(default=None, ge=0)
    days_of_supply: float | None = Field(default=None, ge=0)
    consequence_cost: float | None = Field(default=None, ge=0)


class InventorySimulationResult(StrictInventoryContract):
    scenario_id: str
    probability_weight: float | None = Field(default=None, ge=0)
    simulation_start_date: date
    simulation_end_date: date
    daily_ledgers: list[DailyInventoryLedger]
    consumption_traces: list[LotConsumptionTrace]
    waste_traces: list[LotWasteTrace]
    expiry_traces: list[LotExpiryTrace]
    ending_lots: list[EndingLotState]
    stockout_dates: list[date]
    summary: InventorySimulationSummary
    accounting_valid: bool
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class InventoryKeyRiskMetrics(StrictInventoryContract):
    store_id: str
    ingredient_id: str
    unit: str
    stockout_probability: float = Field(ge=0, le=1)
    expected_shortage: float = Field(ge=0)
    p50_shortage: float = Field(ge=0)
    p95_shortage: float = Field(ge=0)
    expected_expired_quantity: float = Field(ge=0)
    expected_explicit_waste: float = Field(ge=0)
    waste_threshold_exceedance_probability: float = Field(ge=0, le=1)
    expected_fill_rate: float = Field(ge=0, le=1)
    fill_rate_below_target_probability: float = Field(ge=0, le=1)
    expected_ending_inventory: float = Field(ge=0)
    p50_ending_inventory: float = Field(ge=0)
    p95_ending_inventory: float = Field(ge=0)
    expected_maximum_inventory: float = Field(ge=0)
    p50_maximum_inventory: float = Field(ge=0)
    p95_maximum_inventory: float = Field(ge=0)
    expected_at_risk_expiry_quantity: float = Field(ge=0)
    p50_at_risk_expiry_quantity: float = Field(ge=0)
    p95_at_risk_expiry_quantity: float = Field(ge=0)
    capacity_violation_probability: float | None = Field(default=None, ge=0, le=1)
    expected_days_of_supply: float | None = Field(default=None, ge=0)
    p50_days_of_supply: float | None = Field(default=None, ge=0)
    p95_days_of_supply: float | None = Field(default=None, ge=0)
    projected_stockout_date_distribution: dict[str, float] = Field(default_factory=dict)
    expected_consequence_cost: float | None = Field(default=None, ge=0)
    p95_consequence_cost: float | None = Field(default=None, ge=0)
    cvar95_consequence_cost: float | None = Field(default=None, ge=0)


class InventoryRiskMetrics(StrictInventoryContract):
    scenario_count: int = Field(ge=1)
    by_key: list[InventoryKeyRiskMetrics]
    any_stockout_probability: float = Field(ge=0, le=1)
    expected_affected_key_count: float = Field(ge=0)
    expected_affected_key_proportion: float = Field(ge=0, le=1)
    mean_key_fill_rate: float = Field(ge=0, le=1)
    any_capacity_violation_probability: float | None = Field(default=None, ge=0, le=1)
    expected_consequence_cost: float | None = Field(default=None, ge=0)
    p95_consequence_cost: float | None = Field(default=None, ge=0)
    cvar95_consequence_cost: float | None = Field(default=None, ge=0)
    # Deprecated compatibility scalars: populated only for one inventory key.
    stockout_probability: float | None = Field(default=None, ge=0, le=1)
    expected_shortage: float | None = Field(default=None, ge=0)
    p50_shortage: float | None = Field(default=None, ge=0)
    p95_shortage: float | None = Field(default=None, ge=0)
    expected_expired_quantity: float | None = Field(default=None, ge=0)
    expected_explicit_waste: float | None = Field(default=None, ge=0)
    waste_threshold_exceedance_probability: float | None = Field(
        default=None, ge=0, le=1
    )
    expected_fill_rate: float | None = Field(default=None, ge=0, le=1)
    fill_rate_below_target_probability: float | None = Field(
        default=None, ge=0, le=1
    )
    expected_ending_inventory: float | None = Field(default=None, ge=0)
    p50_ending_inventory: float | None = Field(default=None, ge=0)
    p95_ending_inventory: float | None = Field(default=None, ge=0)
    expected_maximum_inventory: float | None = Field(default=None, ge=0)
    p50_maximum_inventory: float | None = Field(default=None, ge=0)
    p95_maximum_inventory: float | None = Field(default=None, ge=0)
    expected_at_risk_expiry_quantity: float | None = Field(default=None, ge=0)
    capacity_violation_probability: float | None = Field(default=None, ge=0, le=1)
    expected_days_of_supply: float | None = Field(default=None, ge=0)
    p50_days_of_supply: float | None = Field(default=None, ge=0)
    p95_days_of_supply: float | None = Field(default=None, ge=0)
    projected_stockout_date_distribution: dict[str, float] | None = None


class InventorySimulationPackage(StrictInventoryContract):
    simulation_start_date: date
    simulation_end_date: date
    results: list[InventorySimulationResult]
    risk_metrics: InventoryRiskMetrics | None = None
    baseline_scenarios: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_probability_weights(self) -> InventorySimulationPackage:
        weights = [result.probability_weight for result in self.results]
        if any(weight is not None for weight in weights) and not all(
            weight is not None for weight in weights
        ):
            raise ValueError("Simulation probability weights cannot be partially missing.")
        if weights and all(weight is not None for weight in weights):
            total = sum(float(weight) for weight in weights if weight is not None)
            if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Probabilistic simulation weights phải có tổng bằng 1.")
        identifiers = [result.scenario_id for result in self.results]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Simulation scenario identifiers must be unique.")
        return self
