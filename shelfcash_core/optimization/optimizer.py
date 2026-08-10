from __future__ import annotations

from shelfcash_core.exceptions import OptimizationNotAvailableError
from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    OptimizationResult,
    StrategyProfile,
)
from shelfcash_core.optimization.deterministic import (
    solve_deterministic_procurement,
)
from shelfcash_core.optimization.resimulation import evaluate_candidate_plan
from shelfcash_core.optimization.stochastic import solve_stochastic_procurement
from shelfcash_core.optimization.strategies import default_strategy_profiles
from shelfcash_core.scenario.lead_time import LeadTimeModel
from shelfcash_core.scenario.shelf_life import ShelfLifeModel


def _profiles(request: OptimizationRequest) -> list[StrategyProfile]:
    defaults = {profile.name: profile for profile in default_strategy_profiles()}
    defaults.update({profile.name: profile for profile in request.strategy_profiles})
    return [defaults[name] for name in ("LEAN", "BALANCED", "PROTECTED")]


def optimize_procurement(
    request: OptimizationRequest,
    *,
    lead_time_model: LeadTimeModel | None = None,
    shelf_life_model: ShelfLifeModel | None = None,
) -> OptimizationResult:
    """Generate candidates with OR and accept them only after exact M4 evaluation."""

    evaluations = {}
    warnings: set[str] = set()
    has_probabilities = bool(request.demand_scenarios) and all(
        scenario.probability_weight is not None
        for scenario in request.demand_scenarios
    )
    use_stochastic = (
        request.stochastic and has_probabilities and len(request.demand_scenarios) > 1
    )
    if request.stochastic and not use_stochastic:
        warnings.add("STOCHASTIC_NOT_AVAILABLE_WITH_UNWEIGHTED_OR_SINGLE_SCENARIO")
    for profile in _profiles(request):
        try:
            plan = (
                solve_stochastic_procurement(request, profile)
                if use_stochastic
                else solve_deterministic_procurement(request, profile)
            )
        except OptimizationNotAvailableError as exc:
            warnings.add(f"{exc.code}: {exc}")
            plan = solve_deterministic_procurement(request, profile)
        evaluations[profile.name] = evaluate_candidate_plan(
            plan,
            request,
            profile,
            lead_time_model=lead_time_model,
            shelf_life_model=shelf_life_model,
        )

    # The profile is a policy preference, not a hard-coded recommendation.
    valid = [(name, item) for name, item in evaluations.items() if item.critic.passed]
    recommended = min(valid, key=lambda item: (item[1].plan.purchase_cost, item[0]))[0] if valid else None
    return OptimizationResult(
        request_id=request.request_id,
        evaluations=evaluations,
        recommended_strategy=recommended,
        status=(
            "COMPLETED" if recommended is not None else "NO_VALID_PROCUREMENT_PLAN"
        ),
        provenance={
            "candidate_engine": "stochastic_saa" if use_stochastic else "deterministic_mip",
            "validation_engine": "m4_lot_level_fefo_v1",
            "recommendation_rule": "lowest_exact_valid_candidate_cost_then_strategy_name",
            "no_valid_plan_reason": (
                None if recommended is not None else "NO_VALID_PROCUREMENT_PLAN"
            ),
            "supply_uncertainty": (
                "externally_realized"
                if lead_time_model is not None
                else "supplier_offer_fixed_lead_time"
            ),
        },
        warnings=sorted(warnings),
    )
