from datetime import date

import pytest

from shelfcash_core.inventory.contracts import (
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.optimization.constraints import validate_plan_constraints
from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    ProcurementDecisionLine,
    ProcurementPlan,
    StrategyProfile,
    SupplierOffer,
)
from shelfcash_core.optimization.critic import critique_procurement_plan
from shelfcash_core.optimization.model_data import supplier_arrival_date


DECISION_DATE = date(2026, 8, 12)  # Wednesday


def _offer(*, delivery_days=None, lead_time_days=1):
    return SupplierOffer(
        offer_id="offer", supplier_id="supplier", store_id="store",
        ingredient_id="ingredient", unit="kg", order_date=DECISION_DATE,
        pack_size=1, unit_price=10, minimum_order_quantity=1,
        lead_time_days=lead_time_days, available_delivery_days=delivery_days,
    )


def _line(arrival_date):
    return ProcurementDecisionLine(
        offer_id="offer", supplier_id="supplier", store_id="store",
        ingredient_id="ingredient", unit="kg", order_date=DECISION_DATE,
        arrival_date=arrival_date, pack_count=1, pack_size=1, order_quantity=1,
        unit_price=10, purchase_cost=10, delivery_cost=0,
    )


def _plan(*, orders=()):
    return ProcurementPlan(
        plan_id="plan", strategy="PROTECTED", orders=list(orders),
        purchase_cost=sum(line.purchase_cost for line in orders), solver_status="OPTIMAL",
    )


def _request(*, quantity, delivery_days=None, lead_time_days=1):
    scenario = InventoryDemandScenario(
        scenario_id="design", probability_weight=None,
        simulation_start_date=DECISION_DATE, simulation_end_date=date(2026, 8, 14),
        lines=[InventoryDemandLine(
            scenario_id="design", store_id="store", ingredient_id="ingredient",
            target_date=date(2026, 8, 14), quantity=100, unit="kg",
        )],
    )
    return OptimizationRequest(
        request_id="critic-test", decision_date=DECISION_DATE,
        planning_end_date=date(2026, 8, 14),
        initial_inventory=[InventoryLot(
            lot_id="initial", store_id="store", ingredient_id="ingredient",
            quantity_remaining=quantity, unit="kg", received_date=date(2026, 8, 11),
            expiry_date=None, source_type="initial_inventory",
        )], demand_scenarios=[scenario],
        supplier_offers=[_offer(delivery_days=delivery_days, lead_time_days=lead_time_days)],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=False,
    )


def _protected_profile():
    return StrategyProfile(
        name="PROTECTED", shortage_penalty=1, holding_penalty=1,
        waste_penalty=1, cash_penalty=0, minimum_expected_fill_rate=0.95,
        minimum_acceptable_fill_rate=0.95,
    )


@pytest.mark.parametrize(
    ("delivery_days", "lead_time_days", "expected"),
    [
        (None, 2, date(2026, 8, 14)),  # no calendar
        ([4], 1, date(2026, 8, 14)),  # Thursday nominal -> Friday
        ([2], 0, date(2026, 8, 12)),  # same-day delivery allowed
        ([4], 0, date(2026, 8, 14)),  # same-day delivery shifted to Friday
        ([0], 1, date(2026, 8, 17)),  # weekday wrap
    ],
)
def test_supplier_arrival_date_uses_the_canonical_delivery_calendar(
    delivery_days, lead_time_days, expected
):
    assert supplier_arrival_date(_offer(
        delivery_days=delivery_days, lead_time_days=lead_time_days
    )) == expected


def test_empty_delivery_calendar_has_no_valid_arrival():
    offer = _offer(delivery_days=[])
    assert supplier_arrival_date(offer) is None
    violations, checks, evidence = validate_plan_constraints(
        _plan(orders=[_line(date(2026, 8, 13))]), [offer]
    )
    assert violations == ["LEAD_TIME:offer"]
    assert checks["lead_time"] is False
    assert evidence["LEAD_TIME:offer"]["reason"] == "delivery_calendar_has_no_available_day"


def test_invalid_delivery_weekday_is_rejected_by_the_supplier_contract():
    with pytest.raises(ValueError, match="weekday values 0..6"):
        _offer(delivery_days=[7])


def test_calendar_adjusted_arrival_passes_critic_and_exposes_no_lead_time_finding():
    request = _request(quantity=100, delivery_days=[4])
    simulation = simulate_inventory_scenarios(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=request.decision_date,
        simulation_end_date=request.planning_end_date,
    )
    critic = critique_procurement_plan(
        _plan(orders=[_line(date(2026, 8, 14))]), request, _protected_profile(), simulation
    )
    assert critic.passed
    assert critic.checks["lead_time"] is True
    assert critic.checks["service_level"] is True
    assert critic.checks["exact_service_floor"] is True
    assert "LEAD_TIME:offer" not in critic.hard_violations
    assert "SERVICE_LEVEL_REQUIREMENT" not in critic.hard_violations
    assert critic.details["service_level_evaluation"]["metric_source"] == (
        "exact_fefo.minimum_key_scenario_fill_rate"
    )


def test_wrong_calendar_arrival_fails_with_actionable_evidence():
    violations, checks, evidence = validate_plan_constraints(
        _plan(orders=[_line(date(2026, 8, 15))]), [_offer(delivery_days=[4])]
    )
    assert violations == ["LEAD_TIME:offer"]
    assert checks["lead_time"] is False
    assert evidence["LEAD_TIME:offer"] == {
        "offer_id": "offer", "order_date": "2026-08-12", "lead_time_days": 1,
        "nominal_arrival_date": "2026-08-13", "available_delivery_days": [4],
        "expected_calendar_adjusted_arrival_date": "2026-08-14",
        "actual_arrival_date": "2026-08-15",
        "reason": "arrival_date_does_not_match_supplier_calendar",
    }


@pytest.mark.parametrize(
    ("quantity", "service_passes"), [(96.1, True), (94, False)])
def test_protected_service_uses_exact_fill_when_weighted_risk_metrics_are_unavailable(
    quantity, service_passes
):
    request = _request(quantity=quantity)
    simulation = simulate_inventory_scenarios(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=request.decision_date,
        simulation_end_date=request.planning_end_date,
    )
    assert simulation.risk_metrics is None
    critic = critique_procurement_plan(_plan(), request, _protected_profile(), simulation)
    assert critic.checks["service_level"] is service_passes
    assert critic.checks["exact_service_floor"] is service_passes
    service = critic.details["service_level_evaluation"]
    assert service["metric_source"] == "exact_fefo.minimum_key_scenario_fill_rate"
    assert service["observed_fill_rate"] == pytest.approx(quantity / 100)
    if service_passes:
        assert "SERVICE_LEVEL_REQUIREMENT" not in critic.hard_violations
    else:
        assert "SERVICE_LEVEL_REQUIREMENT" in critic.hard_violations
        assert critic.details["finding_evidence"]["SERVICE_LEVEL_REQUIREMENT"]["observed_fill_rate"] == pytest.approx(.94)


@pytest.mark.parametrize(
    ("name", "floor", "quantity", "passes"),
    [
        ("BALANCED", .90, 91, True),
        ("BALANCED", .90, 89, False),
        ("LEAN", .80, 81, True),
        ("LEAN", .80, 79, False),
    ],
)
def test_exact_safety_floor_preserves_lean_and_balanced_policy(name, floor, quantity, passes):
    request = _request(quantity=quantity)
    simulation = simulate_inventory_scenarios(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=request.decision_date,
        simulation_end_date=request.planning_end_date,
    )
    profile = StrategyProfile(
        name=name, shortage_penalty=1, holding_penalty=1, waste_penalty=1,
        cash_penalty=0, minimum_acceptable_fill_rate=floor,
    )
    critic = critique_procurement_plan(_plan(), request, profile, simulation)
    assert critic.checks["exact_service_floor"] is passes
    assert ("EXACT_SIMULATION_SAFETY_FLOOR" not in critic.hard_violations) is passes
