"""Acceptance coverage for stochastic scenario-evidence authority."""

from datetime import date, timedelta

import pytest

from app.schemas.decision import DecisionPackageTechnicalMetrics
from shelfcash_core.inventory.contracts import (
    InventoryDemandLine,
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPolicy,
    ConsequenceCostAssumption,
)
from shelfcash_core.inventory.monte_carlo import MonteCarloInventoryRunner
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.optimization.critic import critique_procurement_plan
from shelfcash_core.optimization.contracts import ProcurementPlan, StrategyProfile
from shelfcash_core.optimization.contracts import OptimizationRequest
from shelfcash_core.optimization.optimizer import optimize_procurement
from shelfcash_core.scenario.sufficiency import (
    MIN_EFFECTIVE_STOCHASTIC_SCENARIOS,
    select_stochastic_scenarios,
)


DAY = date(2026, 8, 20)


def _scenario(name: str, quantity: float, weight: float) -> InventoryDemandScenario:
    return InventoryDemandScenario(
        scenario_id=name,
        probability_weight=weight,
        simulation_start_date=DAY,
        simulation_end_date=DAY,
        lines=[InventoryDemandLine(
            scenario_id=name,
            store_id="STORE_001",
            ingredient_id="milk",
            target_date=DAY,
            quantity=quantity,
            unit="liter",
        )],
    )


def _design() -> list[InventoryDemandScenario]:
    return [
        _scenario("p25_design", 2, None),
        _scenario("p50_design", 3, None),
        _scenario("p75_design", 4, None),
    ]


def test_duplicate_stochastic_paths_collapse_and_fall_back_to_design_scenarios():
    selection = select_stochastic_scenarios(
        [_scenario(f"generated-{index}", 10, 0.2) for index in range(5)],
        _design(),
    )

    assert selection.scenario_count_generated == 5
    assert selection.unique_scenario_count == 1
    assert selection.effective_scenario_count == pytest.approx(1.0)
    assert selection.stochastic_saa_enabled is False
    assert selection.fallback_reason == "insufficient_effective_scenarios"
    assert [scenario.scenario_id for scenario in selection.optimizer_scenarios] == [
        "p25_design", "p50_design", "p75_design"
    ]
    assert selection.risk_scenarios == []


def test_sufficient_unique_weighted_paths_keep_stochastic_authority():
    selection = select_stochastic_scenarios(
        [_scenario(f"generated-{index}", index + 1, 1 / 10) for index in range(10)],
        _design(),
    )

    assert MIN_EFFECTIVE_STOCHASTIC_SCENARIOS == 10
    assert selection.scenario_count_generated == 10
    assert selection.unique_scenario_count == 10
    assert selection.effective_scenario_count == pytest.approx(10.0)
    assert selection.stochastic_saa_enabled is True
    assert selection.fallback_reason is None
    assert len(selection.optimizer_scenarios) == 10
    assert len(selection.risk_scenarios) == 10


def test_effective_count_uses_aggregated_path_weights_not_record_count():
    selection = select_stochastic_scenarios(
        [
            _scenario("first-copy", 1, 0.25),
            _scenario("second-copy", 1, 0.25),
            _scenario("distinct", 2, 0.5),
        ],
        _design(),
    )

    # Duplicate records collapse to weights [0.5, 0.5], so N_eff is two.
    assert selection.unique_scenario_count == 2
    assert selection.effective_scenario_count == pytest.approx(2.0)
    assert selection.stochastic_saa_enabled is False


def test_duplicate_path_weights_are_aggregated_before_a_sufficient_gate():
    scenarios = [
        _scenario(f"copy-{quantity}-{copy}", quantity, 0.05)
        for quantity in range(10)
        for copy in range(2)
    ]
    selection = select_stochastic_scenarios(scenarios, _design())

    assert selection.scenario_count_generated == 20
    assert selection.unique_scenario_count == 10
    assert selection.effective_scenario_count == pytest.approx(10.0)
    assert selection.stochastic_saa_enabled is True
    assert [scenario.probability_weight for scenario in selection.risk_scenarios] == [
        pytest.approx(0.1)
    ] * 10


def test_old_decision_package_metrics_remain_readable_without_new_diagnostics():
    metrics = DecisionPackageTechnicalMetrics.model_validate({
        "scenario_count": 3,
        "scenario_method": "quantile_design",
        "random_seed": 7,
        "optimizer_type": "deterministic_mip",
        "core_version": "v1",
        "stochastic_saa_enabled": False,
        "baseline_engine": "lot_level_fefo_v1",
    })

    assert metrics.scenario_count_generated is None
    assert metrics.unique_scenario_count is None
    assert metrics.effective_scenario_count is None
    assert metrics.stochastic_fallback_reason is None


def _request(scenarios, *, stochastic: bool, initial_quantity: float) -> OptimizationRequest:
    return OptimizationRequest(
        request_id="sufficiency", decision_date=DAY, planning_end_date=DAY,
        initial_inventory=[InventoryLot(
            lot_id="milk", store_id="STORE_001", ingredient_id="milk",
            quantity_remaining=initial_quantity, unit="liter", received_date=DAY - timedelta(days=1),
            expiry_date=None, source_type="initial_inventory",
        )],
        demand_scenarios=scenarios,
        supplier_offers=[],
        cost_assumptions=[ConsequenceCostAssumption(
            store_id="STORE_001", ingredient_id="milk", unit="liter",
        )],
        inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last"),
        stochastic=stochastic,
    )


def _protected_profile() -> StrategyProfile:
    return StrategyProfile(
        name="PROTECTED", shortage_penalty=1, holding_penalty=1,
        waste_penalty=1, cash_penalty=0, minimum_acceptable_fill_rate=0.55,
        maximum_acceptable_stockout_probability=0.1,
        maximum_stockout_probability=0.1,
    )


def _empty_plan() -> ProcurementPlan:
    return ProcurementPlan(
        plan_id="sufficiency-plan", strategy="PROTECTED", orders=[],
        purchase_cost=0, solver_status="OPTIMAL",
    )


def test_insufficient_evidence_disables_risk_authority_but_exact_floor_still_rejects():
    selection = select_stochastic_scenarios(
        [_scenario(f"duplicate-{index}", 10, 0.2) for index in range(5)],
        [_scenario("p75_design", 10, None)],
    )
    request = _request(
        selection.optimizer_scenarios,
        stochastic=selection.stochastic_saa_enabled,
        initial_quantity=5,
    )
    simulation = simulate_inventory_scenarios(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=DAY, simulation_end_date=DAY,
    )
    critic = critique_procurement_plan(_empty_plan(), request, _protected_profile(), simulation)

    assert selection.stochastic_saa_enabled is False
    assert "RISK_CONSTRAINT_VIOLATION" not in critic.hard_violations
    assert "EXACT_SIMULATION_SAFETY_FLOOR" in critic.hard_violations


def test_sufficient_high_stochastic_risk_remains_a_hard_rejection():
    selection = select_stochastic_scenarios(
        [_scenario(f"high-{index}", 10 + index, 0.1) for index in range(10)],
        _design(),
    )
    request = _request(
        selection.optimizer_scenarios,
        stochastic=selection.stochastic_saa_enabled,
        initial_quantity=5,
    )
    simulation = MonteCarloInventoryRunner().run(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=DAY, simulation_end_date=DAY, seed=7,
    )
    critic = critique_procurement_plan(_empty_plan(), request, _protected_profile(), simulation)

    assert selection.stochastic_saa_enabled is True
    assert "RISK_CONSTRAINT_VIOLATION" in critic.hard_violations


def test_sufficient_low_stochastic_risk_does_not_emit_risk_violation():
    selection = select_stochastic_scenarios(
        [_scenario(f"low-{index}", index, 0.1) for index in range(10)],
        _design(),
    )
    request = _request(
        selection.optimizer_scenarios,
        stochastic=selection.stochastic_saa_enabled,
        initial_quantity=100,
    )
    simulation = MonteCarloInventoryRunner().run(
        request.initial_inventory, request.demand_scenarios, request.existing_inbound,
        policy=request.inventory_policy, simulation_start_date=DAY, simulation_end_date=DAY, seed=7,
    )
    critic = critique_procurement_plan(_empty_plan(), request, _protected_profile(), simulation)

    assert selection.stochastic_saa_enabled is True
    assert "RISK_CONSTRAINT_VIOLATION" not in critic.hard_violations


def test_deterministic_request_remains_unweighted_and_unchanged():
    result = optimize_procurement(_request(_design(), stochastic=False, initial_quantity=100))

    assert result.provenance["candidate_engine"] == "deterministic_mip"
    assert "RISK_CONSTRAINT_VIOLATION" not in result.evaluations["PROTECTED"].critic.hard_violations
