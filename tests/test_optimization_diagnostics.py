from datetime import date

from shelfcash_core.inventory.contracts import (
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.optimization.contracts import OptimizationRequest, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement


def _request(*, demand_days, probability_weight=None, unit_price=1000, lead_time_days=0):
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
        initial_inventory=[],
        demand_scenarios=[scenario],
        supplier_offers=[
            SupplierOffer(
                offer_id="offer", supplier_id="supplier", store_id="store",
                ingredient_id="ingredient", unit="kg", order_date=decision_date,
                pack_size=1, unit_price=unit_price, minimum_order_quantity=1,
                lead_time_days=lead_time_days,
            )
        ],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=False,
    )


def test_empty_order_has_objective_reason_and_breakdown():
    result = optimize_procurement(
        _request(demand_days=[(date(2026, 8, 12), 1)], unit_price=1000)
    )
    plan = result.evaluations["PROTECTED"].plan

    assert plan.orders == []
    assert plan.provenance["no_order_diagnostics"] == [{
        "ingredient_id": "ingredient",
        "unit": "kg",
        "demand_by_date": {"2026-08-12": 1.0},
        "usable_initial_inventory": 0.0,
        "open_inbound_by_date": {},
        "first_shortage_date_without_purchase": "2026-08-12",
        "budget_limit": None,
        "no_order_reason": "NO_PURCHASE_OBJECTIVE_PREFERS_SHORTAGE",
        "offers": [{
            "supplier_id": "supplier", "offer_id": "offer", "available": True,
            "unit": "kg", "unit_conversion_factor": 1.0, "moq": 1.0,
            "pack_size": 1.0, "maximum_order_quantity": None,
            "lead_time_days": 0, "order_date": "2026-08-12",
            "arrival_date": "2026-08-12", "unit_purchase_price": 1000.0,
        }],
    }]
    breakdown = plan.provenance["objective_breakdown"]
    assert breakdown["purchase_term"] == 0
    assert breakdown["shortage_term"] == 50
    assert breakdown["total_objective"] == 50


def test_purchase_with_lead_time_can_cover_later_horizon_demand():
    result = optimize_procurement(
        _request(
            demand_days=[(date(2026, 8, 12), 1), (date(2026, 8, 14), 1)],
            unit_price=1,
            lead_time_days=2,
        )
    )
    plan = result.evaluations["PROTECTED"].plan

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
