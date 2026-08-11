"""Fixed-plan, weighted Exact FEFO risk regression coverage."""

from datetime import date

from app.services.business_metrics_service import build_business_metrics
from shelfcash_core.inventory.contracts import (
    ConsequenceCostAssumption,
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.optimization.contracts import OptimizationRequest
from shelfcash_core.optimization.optimizer import optimize_procurement


DAY = date(2026, 8, 12)


def _scenario(name, quantity, weight):
    return InventoryDemandScenario(
        scenario_id=name,
        probability_weight=weight,
        simulation_start_date=DAY,
        simulation_end_date=DAY,
        lines=[InventoryDemandLine(
            scenario_id=name, store_id="store", ingredient_id="milk",
            target_date=DAY, quantity=quantity, unit="L",
        )],
    )


def _request(*, risk_scenarios):
    design = _scenario("p50_design", 5, None)
    return OptimizationRequest(
        request_id="risk", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[InventoryLot(
            lot_id="milk", store_id="store", ingredient_id="milk",
            quantity_remaining=5, unit="L", received_date=date(2026, 8, 11),
            expiry_date=date(2026, 8, 20), source_type="initial_inventory",
        )],
        demand_scenarios=[design], risk_demand_scenarios=risk_scenarios,
        supplier_offers=[], stochastic=False,
        cost_assumptions=[ConsequenceCostAssumption(
            store_id="store", ingredient_id="milk", unit="L",
        )],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        risk_evaluation_metadata={
            "status": "evaluated", "method": "empirical_residual_bootstrap",
            "sample_count": len(risk_scenarios), "seed": 7,
        },
    )


def test_fixed_plan_risk_is_weighted_exact_fefo_and_unit_safe():
    result = optimize_procurement(_request(risk_scenarios=[
        _scenario("s1", 5, 0.7), _scenario("s2", 10, 0.3),
    ]))
    evaluation = result.evaluations["BALANCED"]

    # No recourse supplier exists: both deterministic plan and initial lot are
    # fixed, while only scenario demand changes.
    assert evaluation.plan.orders == []
    risk = evaluation.risk_simulation.risk_metrics
    assert risk.any_stockout_probability == 0.3
    assert risk.mean_key_fill_rate == 0.85
    assert risk.by_key[0].expected_shortage == 1.5

    metrics = build_business_metrics(
        purchase_cost=0, simulation=evaluation.simulation,
        risk_simulation=evaluation.risk_simulation, recommended=True,
        risk_metadata=_request(risk_scenarios=[]).risk_evaluation_metadata,
    )
    probabilistic = metrics["probabilistic"]
    assert probabilistic["status"] == "evaluated"
    assert probabilistic["stockout_probability"] == 0.3
    assert probabilistic["expected_fill_rate"] == 0.85
    assert probabilistic["expected_shortage"] is None
    assert probabilistic["expected_shortage_by_ingredient"] == [
        {"ingredient_id": "milk", "unit": "liter", "quantity": 1.5}
    ]


def test_probability_metrics_remain_unavailable_without_valid_distribution():
    request = _request(risk_scenarios=[])
    result = optimize_procurement(request)
    evaluation = result.evaluations["BALANCED"]
    metrics = build_business_metrics(
        purchase_cost=0, simulation=evaluation.simulation, recommended=True,
        risk_metadata={
            "status": "not_evaluated",
            "reason": "RESIDUAL_DISTRIBUTION_NOT_AVAILABLE",
        },
    )
    assert metrics["probabilistic"]["status"] == "not_evaluated"
    assert metrics["probabilistic"]["stockout_probability"] is None
