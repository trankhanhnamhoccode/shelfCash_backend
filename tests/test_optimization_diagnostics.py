from datetime import date

from shelfcash_core.inventory.contracts import (
    ConsequenceCostAssumption,
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.optimization.contracts import OptimizationRequest, StrategyProfile, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement
from shelfcash_core.optimization.model_data import supplier_arrival_date
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios


def _request(*, demand_days, probability_weight=None, unit_price=1000, lead_time_days=0,
             shortage_cost=1, pack_size=1, moq=1, budget=None, initial_inventory=None):
    decision_date = date(2026, 8, 12)
    scenario = InventoryDemandScenario(
        scenario_id="design",
        probability_weight=probability_weight,
        simulation_start_date=decision_date,
        simulation_end_date=date(2026, 8, 15),
        lines=[
            InventoryDemandLine(
                scenario_id="design", store_id="store", ingredient_id="ingredient",
                target_date=day, quantity=quantity, unit="kg",
            )
            for day, quantity in demand_days
        ],
    )
    return OptimizationRequest(
        request_id="test-request",
        decision_date=decision_date,
        planning_end_date=date(2026, 8, 15),
        initial_inventory=initial_inventory or [],
        demand_scenarios=[scenario],
        supplier_offers=[
            SupplierOffer(
                offer_id="offer", supplier_id="supplier", store_id="store",
                ingredient_id="ingredient", unit="kg", order_date=decision_date,
                pack_size=pack_size, unit_price=unit_price, minimum_order_quantity=moq,
                lead_time_days=lead_time_days,
            )
        ],
        budget=budget,
        cost_assumptions=[ConsequenceCostAssumption(
            store_id="store", ingredient_id="ingredient", unit="kg",
            shortage_cost_per_unit=shortage_cost,
        )],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=False,
    )


def test_empty_order_has_objective_reason_and_breakdown():
    result = optimize_procurement(
        _request(demand_days=[(date(2026, 8, 12), 1)], unit_price=1000)
    )
    plan = result.evaluations["LEAN"].plan

    assert plan.orders == []
    diagnostic = plan.provenance["no_order_diagnostics"][0]
    assert diagnostic["no_order_reason"] == "NO_PURCHASE_OBJECTIVE_PREFERS_SHORTAGE"
    assert diagnostic["purchase_candidate"]["cost"] == 1000
    assert diagnostic["no_purchase_consequence"]["shortage_cost"] == 1
    breakdown = plan.provenance["objective_breakdown"]
    assert breakdown["purchase_term"] == 0
    assert breakdown["shortage_term"] == 1
    assert breakdown["total_objective"] == 1


def test_buys_when_configured_shortage_consequence_is_more_expensive():
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 10)], unit_price=10_000,
        shortage_cost=30_000,
    )).evaluations["BALANCED"].plan
    assert plan.orders and plan.orders[0].order_quantity == 10
    assert plan.provenance["objective_breakdown"]["purchase_term"] > 0
    assert plan.provenance["objective_breakdown"]["shortage_term"] == 0
    assert plan.provenance["no_order_diagnostics"] == []


def test_shortage_can_be_economically_rational_when_it_is_cheaper_than_buying():
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 10)], unit_price=50_000,
        shortage_cost=10_000,
    )).evaluations["LEAN"].plan
    assert plan.orders == []
    assert plan.provenance["no_order_diagnostics"][0]["no_order_reason"] == "NO_PURCHASE_OBJECTIVE_PREFERS_SHORTAGE"


def test_strategy_multiplier_changes_borderline_economic_decision():
    result = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 1)], unit_price=100,
        shortage_cost=10,
    ))
    assert result.evaluations["LEAN"].plan.orders == []
    assert result.evaluations["PROTECTED"].plan.orders


def test_moq_pack_cost_is_compared_using_feasible_pack_not_shortage_quantity():
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 3)], unit_price=20,
        shortage_cost=5, pack_size=10, moq=10,
    )).evaluations["BALANCED"].plan
    assert plan.orders == []
    diagnostic = plan.provenance["no_order_diagnostics"][0]
    assert diagnostic["purchase_candidate"]["quantity"] == 10
    assert diagnostic["purchase_candidate"]["cost"] == 200


def test_budget_blocked_is_not_reported_as_objective_preference():
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 1)], unit_price=100,
        shortage_cost=100, budget=50,
    )).evaluations["PROTECTED"].plan
    assert plan.orders == []
    assert plan.provenance["no_order_diagnostics"][0]["no_order_reason"] == "NO_PURCHASE_BUDGET_BLOCKED"


def test_inventory_sufficient_has_no_purchase_reason():
    inventory = [InventoryLot(
        lot_id="lot", store_id="store", ingredient_id="ingredient",
        quantity_remaining=1, unit="kg", received_date=date(2026, 8, 1),
        expiry_date=date(2026, 8, 20), source_type="initial_inventory",
    )]
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 1)], initial_inventory=inventory,
    )).evaluations["PROTECTED"].plan
    assert plan.orders == []
    assert plan.provenance["no_order_diagnostics"][0]["no_order_reason"] == "NO_PURCHASE_INVENTORY_SUFFICIENT"


def test_explicit_protected_service_target_is_enforced_in_deterministic_milp():
    request = _request(demand_days=[(date(2026, 8, 12), 1)], unit_price=100, shortage_cost=1)
    request = request.model_copy(update={"strategy_profiles": [StrategyProfile(
        name="PROTECTED", shortage_penalty=1, holding_penalty=1, waste_penalty=1,
        cash_penalty=0, minimum_expected_fill_rate=0.95,
    )]})
    plan = optimize_procurement(request).evaluations["PROTECTED"].plan
    assert plan.orders
    assert plan.provenance["deterministic_service_constraint"] == "aggregate_design_fill_rate"


def test_infeasible_protected_service_target_does_not_create_fake_purchase():
    request = _request(
        demand_days=[(date(2026, 8, 12), 1)], unit_price=1, shortage_cost=1,
        lead_time_days=4,
    )
    request = request.model_copy(update={"strategy_profiles": [StrategyProfile(
        name="PROTECTED", shortage_penalty=1, holding_penalty=1, waste_penalty=1,
        cash_penalty=0, minimum_expected_fill_rate=0.95,
    )]})
    plan = optimize_procurement(request).evaluations["PROTECTED"].plan
    assert plan.solver_status == "INFEASIBLE"
    assert plan.orders == []


def test_expiry_bucket_allows_demand_through_expiry_date_but_not_after():
    inventory = [InventoryLot(
        lot_id="expiring", store_id="store", ingredient_id="ingredient",
        quantity_remaining=10, unit="kg", received_date=date(2026, 8, 1),
        expiry_date=date(2026, 8, 13), source_type="initial_inventory",
    )]
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 4), (date(2026, 8, 13), 3), (date(2026, 8, 14), 3)],
        unit_price=10_000, initial_inventory=inventory,
    )).evaluations["LEAN"].plan
    ledger = plan.provenance["chronology_ledger"]
    assert [item["shortage"] for item in ledger] == [0, 0, 3, 0]
    assert ledger[2]["expiry_loss"] == 3


def test_two_expiry_buckets_preserve_later_lot_after_early_lot_expires():
    inventory = [
        InventoryLot(lot_id="early", store_id="store", ingredient_id="ingredient", quantity_remaining=5, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 12), source_type="initial_inventory"),
        InventoryLot(lot_id="late", store_id="store", ingredient_id="ingredient", quantity_remaining=10, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 17), source_type="initial_inventory"),
    ]
    plan = optimize_procurement(_request(
        demand_days=[(date(2026, 8, 12), 5), (date(2026, 8, 14), 10)], unit_price=10_000,
        initial_inventory=inventory,
    )).evaluations["LEAN"].plan
    assert sum(item["shortage"] for item in plan.provenance["chronology_ledger"]) == 0


def test_supplier_delivery_calendar_shifts_arrival_after_nominal_lead_time():
    offer = SupplierOffer(
        offer_id="calendar", supplier_id="supplier", store_id="store", ingredient_id="ingredient",
        unit="kg", order_date=date(2026, 8, 12), pack_size=1, unit_price=1,
        lead_time_days=1, available_delivery_days=[4],  # Friday; nominal Thursday.
    )
    assert supplier_arrival_date(offer) == date(2026, 8, 14)


def test_expiry_bucket_preserves_exact_total_service_but_not_fefo_daily_allocation():
    inventory = [
        InventoryLot(lot_id="early", store_id="store", ingredient_id="ingredient", quantity_remaining=5, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 12), source_type="initial_inventory"),
        InventoryLot(lot_id="late", store_id="store", ingredient_id="ingredient", quantity_remaining=5, unit="kg", received_date=date(2026, 8, 1), expiry_date=date(2026, 8, 14), source_type="initial_inventory"),
    ]
    request = _request(
        demand_days=[(date(2026, 8, 12), 4), (date(2026, 8, 13), 3), (date(2026, 8, 14), 3)],
        unit_price=10_000, initial_inventory=inventory,
    )
    plan = optimize_procurement(request).evaluations["LEAN"].plan
    exact = simulate_inventory_scenarios(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=request.decision_date,
        simulation_end_date=request.planning_end_date,
    ).results[0].daily_ledgers
    predicted = plan.provenance["chronology_ledger"]
    assert sum(row["served"] for row in predicted) == sum(row.fulfilled_quantity for row in exact)
    assert sum(row["shortage"] for row in predicted) == sum(row.shortage_quantity for row in exact)
    # Buckets prevent expired supply from crossing its expiry boundary, but do
    # not impose FEFO selection between simultaneously usable buckets.
    assert predicted[1]["shortage"] != exact[1].shortage_quantity


def test_purchase_with_lead_time_can_cover_later_horizon_demand():
    result = optimize_procurement(
        _request(
            demand_days=[(date(2026, 8, 12), 1), (date(2026, 8, 14), 1)],
            unit_price=1,
            lead_time_days=2,
        )
    )
    plan = result.evaluations["BALANCED"].plan

    assert len(plan.orders) == 1
    assert plan.orders[0].arrival_date == date(2026, 8, 14)
    assert plan.orders[0].order_quantity == 1


def test_unweighted_design_scenarios_do_not_create_a_risk_violation():
    result = optimize_procurement(
        _request(demand_days=[(date(2026, 8, 12), 1)], probability_weight=None, unit_price=1)
    )
    critic = result.evaluations["PROTECTED"].critic

    assert "RISK_CONSTRAINT_VIOLATION" not in critic.hard_violations
    assert "RISK_METRIC_NOT_AVAILABLE" in critic.warnings
    assert critic.details["risk_evaluation"]["status"] == "not_evaluated"
