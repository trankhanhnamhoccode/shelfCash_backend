from datetime import date

from shelfcash_core.inventory.contracts import (
    InboundDelivery, InventoryDemandLine, InventoryDemandScenario, InventorySimulationPolicy,
)
from shelfcash_core.optimization.adapters import decisions_to_planned_inbound
from shelfcash_core.optimization.contracts import (
    OptimizationRequest, ProcurementDecisionLine, SupplierOffer,
)
from shelfcash_core.optimization.model_data import build_problem_data
from shelfcash_core.optimization.optimizer import optimize_procurement


DAY = date(2026, 8, 12)


def _request(*, shelf_life_days, delivery_days=None, demand):
    end = max(day for day, _ in demand)
    return OptimizationRequest(
        request_id="shelf-life", decision_date=DAY, planning_end_date=end,
        initial_inventory=[],
        demand_scenarios=[InventoryDemandScenario(
            scenario_id="design", probability_weight=None, simulation_start_date=DAY,
            simulation_end_date=end, lines=[InventoryDemandLine(
                scenario_id="design", store_id="store", ingredient_id="milk",
                target_date=day, quantity=quantity, unit="lít") for day, quantity in demand])],
        supplier_offers=[SupplierOffer(
            offer_id="offer", supplier_id="supplier", store_id="store", ingredient_id="milk",
            unit="lít", order_date=DAY, pack_size=1, unit_price=1,
            minimum_order_quantity=1, lead_time_days=1,
            available_delivery_days=delivery_days, shelf_life_days=shelf_life_days)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=False,
    )


def test_known_shelf_life_uses_actual_calendar_adjusted_arrival_and_exact_fefo_expiry():
    request = _request(
        shelf_life_days=2, delivery_days=[4],  # Thu nominal -> Fri Aug 14
        demand=[(date(2026, 8, 14), 1), (date(2026, 8, 16), 1), (date(2026, 8, 17), 1)],
    )
    data = build_problem_data(request)
    assert data.regular_offers[0].arrival_date == date(2026, 8, 14)
    assert data.regular_offers[0].expiry_date == date(2026, 8, 16)

    result = optimize_procurement(request)
    plan = result.evaluations["BALANCED"].plan
    line = plan.orders[0]
    assert line.arrival_date == date(2026, 8, 14)
    assert line.projected_expiry_date == date(2026, 8, 16)
    inbound = decisions_to_planned_inbound(plan.orders, plan_id=plan.plan_id)
    assert inbound[0].expiry_date == line.projected_expiry_date

    exact = result.evaluations["BALANCED"].simulation.results[0]
    # Inclusive expiry: stock can serve Aug 16 but cannot serve Aug 17.
    ledgers = {row.simulation_date: row for row in exact.daily_ledgers}
    assert ledgers[date(2026, 8, 16)].fulfilled_quantity == 1
    assert ledgers[date(2026, 8, 17)].shortage_quantity == 1
    assert exact.summary.by_key[0].expired_quantity == 0


def test_unknown_shelf_life_is_explicit_not_fake_non_perishable_metadata():
    request = _request(shelf_life_days=None, demand=[(date(2026, 8, 13), 1)])
    data = build_problem_data(request)
    assert data.regular_offers[0].expiry_date is None
    assert "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED" in data.warnings
    result = optimize_procurement(request)
    plan = result.evaluations["BALANCED"].plan
    assert plan.orders[0].projected_expiry_date is None
    assert "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED" in plan.warnings
    assert "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED" in result.evaluations["BALANCED"].simulation.warnings


def test_zero_shelf_life_means_usable_on_arrival_only():
    request = _request(
        shelf_life_days=0,
        demand=[(date(2026, 8, 13), 1), (date(2026, 8, 14), 1)],
    )
    result = optimize_procurement(request)
    exact = result.evaluations["BALANCED"].simulation.results[0]
    ledgers = {row.simulation_date: row for row in exact.daily_ledgers}
    assert ledgers[date(2026, 8, 13)].fulfilled_quantity == 1
    assert ledgers[date(2026, 8, 14)].shortage_quantity == 1


def test_known_open_inbound_expiry_uses_the_same_bounded_bucket_as_exact_fefo():
    request = OptimizationRequest(
        request_id="open-po", decision_date=DAY, planning_end_date=date(2026, 8, 14),
        initial_inventory=[], supplier_offers=[], stochastic=False,
        demand_scenarios=[InventoryDemandScenario(
            scenario_id="design", probability_weight=None, simulation_start_date=DAY,
            simulation_end_date=date(2026, 8, 14), lines=[
                InventoryDemandLine(scenario_id="design", store_id="store", ingredient_id="milk",
                                    target_date=date(2026, 8, 13), quantity=1, unit="lít"),
                InventoryDemandLine(scenario_id="design", store_id="store", ingredient_id="milk",
                                    target_date=date(2026, 8, 14), quantity=1, unit="lít"),
            ])],
        existing_inbound=[InboundDelivery(
            delivery_id="open", lot_id="open-lot", purchase_order_id="po", supplier_id="supplier",
            store_id="store", ingredient_id="milk", quantity=2, unit="lít",
            arrival_date=date(2026, 8, 13), expiry_date=date(2026, 8, 13))],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
    )
    data = build_problem_data(request)
    assert data.existing_inbound_expiry_buckets[0].expiry_date == date(2026, 8, 13)
    result = optimize_procurement(request)
    ledgers = {row.simulation_date: row for row in result.evaluations["BALANCED"].simulation.results[0].daily_ledgers}
    assert ledgers[date(2026, 8, 13)].fulfilled_quantity == 1
    assert ledgers[date(2026, 8, 14)].shortage_quantity == 1


def test_projected_expiry_is_preserved_by_plan_to_inbound_adapter():
    line = ProcurementDecisionLine(
        offer_id="offer", supplier_id="supplier", store_id="store", ingredient_id="milk",
        unit="lít", order_date=DAY, arrival_date=date(2026, 8, 14), pack_count=1,
        pack_size=10, order_quantity=10, unit_price=1, purchase_cost=10,
        delivery_cost=0, shelf_life_days=2, projected_expiry_date=date(2026, 8, 16),
    )
    inbound = decisions_to_planned_inbound([line], plan_id="plan")
    assert inbound[0].expiry_date == date(2026, 8, 16)
