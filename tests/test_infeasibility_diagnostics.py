from datetime import date

from shelfcash_core.inventory.contracts import InventoryDemandLine, InventoryDemandScenario, InventorySimulationPolicy
from shelfcash_core.optimization.contracts import OptimizationRequest, StrategyProfile, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement


DAY = date(2026, 8, 12)


def _request(*, offers=(), budget=None, service=.95):
    return OptimizationRequest(
        request_id="infeasible", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[], supplier_offers=list(offers), budget=budget,
        demand_scenarios=[InventoryDemandScenario(
            scenario_id="d", probability_weight=None, simulation_start_date=DAY,
            simulation_end_date=DAY, lines=[InventoryDemandLine(
                scenario_id="d", store_id="store", ingredient_id="milk", target_date=DAY,
                quantity=10, unit="kg")])],
        strategy_profiles=[StrategyProfile(name="PROTECTED", shortage_penalty=1, holding_penalty=1,
                                           waste_penalty=1, cash_penalty=0,
                                           minimum_expected_fill_rate=service)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"), stochastic=False,
    )


def test_no_supply_source_has_proven_candidate_diagnostic():
    plan = optimize_procurement(_request()).evaluations["PROTECTED"].plan
    diagnostics = plan.provenance["infeasibility_diagnostics"]
    assert any(item["code"] == "NO_FEASIBLE_SUPPLY_SOURCE" and item["confidence"] == "proven" for item in diagnostics)
    assert plan.provenance["infeasibility_diagnostic_probe_count"] == 3


def test_budget_probe_is_counterfactual_and_does_not_change_candidate_plan():
    offer = SupplierOffer(offer_id="offer", supplier_id="s", store_id="store", ingredient_id="milk",
                          unit="kg", order_date=DAY, pack_size=10, unit_price=10,
                          minimum_order_quantity=10, lead_time_days=0)
    plan = optimize_procurement(_request(offers=[offer], budget=1, service=.95)).evaluations["PROTECTED"].plan
    diagnostics = plan.provenance["infeasibility_diagnostics"]
    assert plan.solver_status == "INFEASIBLE"
    assert any(item["code"] == "BUDGET_CONSTRAINT_CONTRIBUTES_TO_INFEASIBILITY" for item in diagnostics)
    assert plan.orders == []
