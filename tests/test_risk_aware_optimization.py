from datetime import date

from shelfcash_core.inventory.contracts import ConsequenceCostAssumption, InventoryDemandLine, InventoryDemandScenario, InventorySimulationPolicy
from shelfcash_core.optimization.contracts import OptimizationRequest, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement


DAY = date(2026, 8, 12)


def _request(weights=(0.8, 0.2), *, stochastic=True):
    scenarios = []
    for name, quantity, weight in (("low", 0, weights[0]), ("high", 10, weights[1])):
        scenarios.append(InventoryDemandScenario(
            scenario_id=name, probability_weight=weight,
            simulation_start_date=DAY, simulation_end_date=DAY,
            lines=[InventoryDemandLine(scenario_id=name, store_id="store", ingredient_id="milk", target_date=DAY, quantity=quantity, unit="L")],
        ))
    return OptimizationRequest(
        request_id="saa", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[], demand_scenarios=scenarios,
        supplier_offers=[SupplierOffer(offer_id="offer", supplier_id="supplier", store_id="store", ingredient_id="milk", unit="L", order_date=DAY, pack_size=10, minimum_order_quantity=10, unit_price=10, lead_time_days=0)],
        cost_assumptions=[ConsequenceCostAssumption(store_id="store", ingredient_id="milk", unit="L", shortage_cost_per_unit=100)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=stochastic,
    )


def test_saa_has_one_shared_order_and_counts_purchase_once():
    evaluation = optimize_procurement(_request()).evaluations["BALANCED"]
    plan = evaluation.plan
    assert len(plan.orders) == 1
    assert plan.orders[0].order_quantity == 10
    assert plan.scenario_recourse_orders == {}
    breakdown = plan.provenance["objective_breakdown"]
    # Balanced's existing cash multiplier applies once to the first-stage
    # purchase; it is not multiplied by the two scenarios.
    assert breakdown["purchase_term"] == 120
    assert plan.provenance["first_stage_non_anticipative"] is True
    assert evaluation.risk_simulation is evaluation.simulation


def test_saa_weights_change_shared_purchase_decision():
    # Balanced's existing shortage multiplier makes the high scenario costly.
    buy = optimize_procurement(_request((0.8, 0.2))).evaluations["BALANCED"].plan
    # 0.05 * (10 * 100) < 100, so the same outcomes with different valid
    # probability weights rationally leave the shared first-stage order at zero.
    no_buy = optimize_procurement(_request((0.999, 0.001))).evaluations["BALANCED"].plan
    assert buy.orders and buy.orders[0].order_quantity == 10
    assert no_buy.orders == []


def test_deterministic_mode_does_not_use_weighted_saa_engine():
    result = optimize_procurement(_request(stochastic=False))
    assert result.provenance["candidate_engine"] == "deterministic_mip"
