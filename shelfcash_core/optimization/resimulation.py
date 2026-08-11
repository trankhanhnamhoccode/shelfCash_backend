from __future__ import annotations

import pandas as pd

from shelfcash_core.exceptions import InventoryError
from shelfcash_core.inventory.monte_carlo import MonteCarloInventoryRunner
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.inventory.stress import run_inventory_stress_tests
from shelfcash_core.optimization.adapters import (
    decisions_to_planned_inbound,
    decisions_to_scenario_planned_inbound,
)
from shelfcash_core.optimization.contracts import (
    CandidateEvaluation,
    OptimizationRequest,
    ProcurementPlan,
    StrategyProfile,
)
from shelfcash_core.optimization.critic import critique_procurement_plan
from shelfcash_core.scenario.lead_time import LeadTimeModel
from shelfcash_core.scenario.shelf_life import ShelfLifeModel


def _with_capacity_context(simulation, request: OptimizationRequest):
    """Attach non-MILP capacity diagnostics to durable Exact FEFO output."""
    if simulation is None or not request.capacity_context:
        return simulation
    store_storage = request.capacity_context.get("store_storage", {})
    results = [
        result.model_copy(update={"provenance": {
            **result.provenance, "capacity_context": request.capacity_context,
            "capacity_evaluation_status": (
                "not_evaluated"
                if store_storage.get("status") == "not_evaluated"
                else result.provenance.get("capacity_evaluation_status", "not_configured")
            ),
        }, "warnings": sorted(set(result.warnings) | (
            {"CAPACITY_NOT_EVALUATED"}
            if store_storage.get("status") == "not_evaluated" else set()
        ))})
        for result in simulation.results
    ]
    return simulation.model_copy(update={"results": results})


def evaluate_candidate_plan(
    plan: ProcurementPlan,
    request: OptimizationRequest,
    profile: StrategyProfile,
    *,
    lead_time_model: LeadTimeModel | None = None,
    shelf_life_model: ShelfLifeModel | None = None,
) -> CandidateEvaluation:
    if (lead_time_model is None) != (shelf_life_model is None):
        raise ValueError(
            "lead_time_model and shelf_life_model must be supplied together."
        )
    base_inbound = (
        decisions_to_planned_inbound(plan.orders, plan_id=plan.plan_id)
        if lead_time_model is None
        else []
    )
    recourse_inbound = {
        scenario_id: decisions_to_planned_inbound(
            lines, plan_id=plan.plan_id, scenario_id=scenario_id
        )
        for scenario_id, lines in plan.scenario_recourse_orders.items()
    }
    if lead_time_model is not None and shelf_life_model is not None:
        realized_base = decisions_to_scenario_planned_inbound(
            plan.orders,
            [scenario.scenario_id for scenario in request.demand_scenarios],
            plan_id=plan.plan_id,
            lead_time_model=lead_time_model,
            shelf_life_model=shelf_life_model,
            seed=request.seed,
        )
        for scenario_id, deliveries in realized_base.items():
            recourse_inbound.setdefault(scenario_id, []).extend(deliveries)
    conversion_frame = pd.DataFrame(
        [rule.model_dump() for rule in request.unit_conversions]
    )
    if conversion_frame.empty:
        conversion_frame = None
    simulation = None
    risk_simulation = None
    stress_simulation = None
    simulation_error = None
    try:
        if all(
            scenario.probability_weight is not None
            for scenario in request.demand_scenarios
        ):
            simulation = MonteCarloInventoryRunner().run(
                request.initial_inventory,
                request.demand_scenarios,
                request.existing_inbound,
                base_inbound,
                scenario_planned_inbound=recourse_inbound,
                policy=request.inventory_policy,
                unit_conversions=conversion_frame,
                cost_assumptions=request.cost_assumptions,
                simulation_start_date=request.decision_date,
                simulation_end_date=request.planning_end_date,
                seed=request.seed,
            )
        else:
            simulation = simulate_inventory_scenarios(
                request.initial_inventory,
                request.demand_scenarios,
                request.existing_inbound,
                base_inbound,
                policy=request.inventory_policy,
                unit_conversions=conversion_frame,
                cost_assumptions=request.cost_assumptions,
                simulation_start_date=request.decision_date,
                simulation_end_date=request.planning_end_date,
            )
        simulation = _with_capacity_context(simulation, request)
        # In risk-aware optimization the canonical optimization scenarios are
        # already weighted and therefore this normal Exact FEFO run is also
        # the risk evaluation.  Do not run it twice.
        if all(s.probability_weight is not None for s in request.demand_scenarios):
            risk_simulation = simulation
    except InventoryError as exc:
        simulation_error = f"{exc.code}: {exc}"
    # Risk evaluation reuses the selected fixed plan and the exact same FEFO
    # transition engine.  It deliberately has no recourse orders and never
    # feeds a relaxed/scenario plan back to the optimizer.
    if simulation is not None and risk_simulation is None and request.risk_demand_scenarios:
        try:
            risk_simulation = MonteCarloInventoryRunner().run(
                request.initial_inventory,
                request.risk_demand_scenarios,
                request.existing_inbound,
                base_inbound,
                policy=request.inventory_policy,
                unit_conversions=conversion_frame,
                cost_assumptions=request.cost_assumptions,
                simulation_start_date=request.decision_date,
                simulation_end_date=request.planning_end_date,
                seed=request.seed,
            )
            risk_simulation = _with_capacity_context(risk_simulation, request)
        except (InventoryError, ValueError) as exc:
            # A failed stochastic evaluation must not silently drop samples or
            # change deterministic candidate feasibility.
            request.risk_evaluation_metadata["status"] = "not_evaluated"
            request.risk_evaluation_metadata["reason"] = "SCENARIO_EVALUATION_FAILED"
            request.risk_evaluation_metadata["error"] = str(exc)
    if simulation is not None and request.stress_scenarios:
        source_id = request.stress_base_scenario_id
        baseline = next(
            (
                scenario
                for scenario in request.demand_scenarios
                if source_id is None or scenario.scenario_id == source_id
            ),
            None,
        )
        if baseline is None:
            simulation_error = "STRESS_BASE_SCENARIO_NOT_FOUND"
        else:
            try:
                stress_planned_inbound = (
                    base_inbound
                    if lead_time_model is None
                    else recourse_inbound.get(baseline.scenario_id, [])
                )
                stress_simulation = run_inventory_stress_tests(
                    request.initial_inventory,
                    baseline,
                    request.stress_scenarios,
                    request.existing_inbound,
                    stress_planned_inbound,
                    policy=request.inventory_policy,
                    unit_conversions=conversion_frame,
                    cost_assumptions=request.cost_assumptions,
                    simulation_start_date=request.decision_date,
                    simulation_end_date=request.planning_end_date,
                )
            except InventoryError as exc:
                simulation_error = f"{exc.code}: {exc}"
    critic = critique_procurement_plan(
        plan,
        request,
        profile,
        simulation,
        stress_simulation=stress_simulation,
        simulation_error=simulation_error,
    )
    completed_plan = plan.model_copy(update={"completed": critic.passed})
    return CandidateEvaluation(
        plan=completed_plan,
        simulation=simulation,
        risk_simulation=risk_simulation,
        stress_simulation=stress_simulation,
        critic=critic,
    )
