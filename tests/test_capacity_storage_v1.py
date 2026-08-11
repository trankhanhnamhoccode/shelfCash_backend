from datetime import date

import pytest

from app.services.business_metrics_service import build_business_metrics
from shelfcash_core.inventory.contracts import (
    ConsequenceCostAssumption, InboundDelivery, InventoryDemandLine,
    InventoryDemandScenario, InventoryLot, InventorySimulationPolicy,
)
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.optimization.contracts import (
    OptimizationRequest, StrategyProfile, SupplierOffer,
)
from shelfcash_core.optimization.critic import critique_procurement_plan
from shelfcash_core.optimization.optimizer import optimize_procurement


DAY = date(2026, 8, 12)


def _scenario(quantity=100):
    return InventoryDemandScenario(
        scenario_id="design", probability_weight=None,
        simulation_start_date=DAY, simulation_end_date=DAY,
        lines=[InventoryDemandLine(scenario_id="design", store_id="store",
                                  ingredient_id="milk", target_date=DAY,
                                  quantity=quantity, unit="lít")],
    )


def _assumption(limit=80):
    return ConsequenceCostAssumption(store_id="store", ingredient_id="milk",
                                     unit="lít", shortage_cost_per_unit=10_000,
                                     capacity_quantity=limit)


def test_exact_capacity_uses_post_receipt_pre_consumption_peak():
    simulation = simulate_inventory_scenarios(
        [InventoryLot(lot_id="initial", store_id="store", ingredient_id="milk",
                      quantity_remaining=20, unit="lít", received_date=date(2026, 8, 11),
                      source_type="initial_inventory")], [_scenario(60)],
        [InboundDelivery(delivery_id="open", lot_id="open-lot", purchase_order_id="po",
                         supplier_id="supplier", store_id="store", ingredient_id="milk",
                         quantity=100, unit="lít", arrival_date=DAY)],
        policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        cost_assumptions=[_assumption(80)], simulation_start_date=DAY,
        simulation_end_date=DAY,
    )
    ledger = simulation.results[0].daily_ledgers[0]
    assert ledger.maximum_quantity == 120
    assert ledger.ending_quantity == 60
    assert ledger.capacity_violation_quantity == 40


def test_deterministic_milp_caps_total_across_expiry_buckets():
    request = OptimizationRequest(
        request_id="capacity", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[InventoryLot(
            lot_id="initial", store_id="store", ingredient_id="milk", quantity_remaining=20,
            unit="lít", received_date=date(2026, 8, 11), expiry_date=date(2026, 8, 20),
            source_type="initial_inventory")], demand_scenarios=[_scenario(100)],
        supplier_offers=[SupplierOffer(
            offer_id="offer", supplier_id="supplier", store_id="store", ingredient_id="milk",
            unit="lít", order_date=DAY, pack_size=1, unit_price=1,
            minimum_order_quantity=1, lead_time_days=0)],
        cost_assumptions=[_assumption(80)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=False,
    )
    plan = optimize_procurement(request).evaluations["BALANCED"].plan
    assert plan.orders[0].order_quantity == 60
    ledger = plan.provenance["chronology_ledger"][0]
    assert ledger["planned_arrivals"] == 60
    assert ledger["ending_usable_inventory"] == 0


def test_capacity_uses_calendar_adjusted_planned_arrival_date():
    request = OptimizationRequest(
        request_id="calendar-capacity", decision_date=DAY,
        planning_end_date=date(2026, 8, 14), initial_inventory=[InventoryLot(
            lot_id="initial", store_id="store", ingredient_id="milk", quantity_remaining=20,
            unit="lít", received_date=date(2026, 8, 11), source_type="initial_inventory")],
        demand_scenarios=[InventoryDemandScenario(
            scenario_id="design", probability_weight=None, simulation_start_date=DAY,
            simulation_end_date=date(2026, 8, 14), lines=[InventoryDemandLine(
                scenario_id="design", store_id="store", ingredient_id="milk",
                target_date=date(2026, 8, 14), quantity=100, unit="lít")])],
        supplier_offers=[SupplierOffer(
            offer_id="offer", supplier_id="supplier", store_id="store", ingredient_id="milk",
            unit="lít", order_date=DAY, pack_size=1, unit_price=1,
            minimum_order_quantity=1, lead_time_days=1, available_delivery_days=[4])],
        cost_assumptions=[_assumption(80)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"), stochastic=False,
    )
    plan = optimize_procurement(request).evaluations["BALANCED"].plan
    assert plan.orders[0].arrival_date == date(2026, 8, 14)
    friday = plan.provenance["chronology_ledger"][-1]
    assert friday["planned_arrivals"] == 60


def test_configured_global_storage_without_volume_metadata_is_not_evaluated():
    request = OptimizationRequest(
        request_id="global-storage", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[], demand_scenarios=[_scenario(1)], supplier_offers=[], stochastic=False,
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        capacity_context={"store_storage": {
            "status": "not_evaluated", "constraint_type": "maximum_storage_volume",
            "capacity_value": 650, "capacity_unit": "lít",
            "reason": "INGREDIENT_STORAGE_VOLUME_METADATA_NOT_CONFIGURED",
        }},
    )
    evaluation = optimize_procurement(request).evaluations["BALANCED"]
    assert evaluation.critic.details["capacity"]["status"] == "not_evaluated"
    assert "CAPACITY_NOT_EVALUATED" in evaluation.critic.warnings
    assert evaluation.simulation.results[0].provenance["capacity_context"]["store_storage"]["reason"] == (
        "INGREDIENT_STORAGE_VOLUME_METADATA_NOT_CONFIGURED"
    )


def test_capacity_critic_and_business_metrics_expose_structured_violation():
    simulation = simulate_inventory_scenarios(
        [InventoryLot(lot_id="initial", store_id="store", ingredient_id="milk",
                      quantity_remaining=20, unit="lít", received_date=date(2026, 8, 11),
                      source_type="initial_inventory")], [_scenario(60)],
        [InboundDelivery(delivery_id="open", lot_id="open-lot", purchase_order_id="po",
                         supplier_id="supplier", store_id="store", ingredient_id="milk",
                         quantity=100, unit="lít", arrival_date=DAY)],
        policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        cost_assumptions=[_assumption(80)], simulation_start_date=DAY, simulation_end_date=DAY,
    )
    from shelfcash_core.optimization.contracts import ProcurementPlan
    critic = critique_procurement_plan(
        ProcurementPlan(plan_id="p", strategy="BALANCED", orders=[], purchase_cost=0,
                        solver_status="OPTIMAL"),
        OptimizationRequest(request_id="r", decision_date=DAY, planning_end_date=DAY,
                            initial_inventory=[], demand_scenarios=[_scenario(60)], supplier_offers=[],
                            stochastic=False),
        StrategyProfile(name="BALANCED", shortage_penalty=1, holding_penalty=1,
                        waste_penalty=1, cash_penalty=0), simulation,
    )
    assert critic.checks["capacity"] is False
    assert "CAPACITY_CONSEQUENCE" in critic.hard_violations
    assert critic.details["finding_evidence"]["CAPACITY_CONSEQUENCE"][0]["excess"] == 40
    metrics = build_business_metrics(purchase_cost=100, simulation=simulation, recommended=True)
    capacity = metrics["deterministic"]["capacity"]
    assert capacity["status"] == "violation"
    assert capacity["constraints"][0]["unit"] == "lít"
    assert capacity["constraints"][0]["excess"] == 40
