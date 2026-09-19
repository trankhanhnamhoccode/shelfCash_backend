"""Authority gate for empirical stochastic demand scenarios.

Generated records are not independent evidence merely because they have
different random scenario IDs.  This module collapses identical business-demand
paths before the Decision adapter grants stochastic optimization/risk authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from math import fsum
from typing import Iterable

from shelfcash_core.inventory.contracts import InventoryDemandScenario


MIN_EFFECTIVE_STOCHASTIC_SCENARIOS = 10


@dataclass(frozen=True)
class StochasticScenarioSelection:
    """Selected scenario authority plus additive sufficiency diagnostics."""

    optimizer_scenarios: list[InventoryDemandScenario]
    risk_scenarios: list[InventoryDemandScenario]
    scenario_count_generated: int
    unique_scenario_count: int
    effective_scenario_count: float
    stochastic_saa_enabled: bool
    fallback_reason: str | None


def _demand_path_identity(scenario: InventoryDemandScenario) -> tuple[tuple[str, str, str, str, str], ...]:
    """Return an exact, deterministic business-demand vector identity.

    Scenario IDs, generator provenance and random metadata intentionally do not
    participate. Float hexadecimal formatting preserves the current exact-float
    contract without introducing an unapproved approximate-equality tolerance.
    """
    return tuple(sorted(
        (
            line.target_date.isoformat(),
            line.store_id,
            line.ingredient_id,
            line.unit,
            float(line.quantity).hex(),
        )
        for line in scenario.lines
    ))


def _collapse_duplicate_paths(
    scenarios: Iterable[InventoryDemandScenario],
) -> list[InventoryDemandScenario]:
    grouped: dict[tuple[tuple[str, str, str, str, str], ...], list[InventoryDemandScenario]] = {}
    for scenario in scenarios:
        if scenario.probability_weight is None:
            raise ValueError("Stochastic scenario evidence requires probability weights.")
        grouped.setdefault(_demand_path_identity(scenario), []).append(scenario)

    collapsed: list[InventoryDemandScenario] = []
    for identity in sorted(grouped):
        members = sorted(grouped[identity], key=lambda scenario: scenario.scenario_id)
        weight = fsum(float(scenario.probability_weight or 0.0) for scenario in members)
        collapsed.append(members[0].model_copy(update={"probability_weight": weight}))
    return collapsed


def select_stochastic_scenarios(
    generated_scenarios: Iterable[InventoryDemandScenario],
    deterministic_design_scenarios: Iterable[InventoryDemandScenario],
    *,
    minimum_effective_scenarios: int = MIN_EFFECTIVE_STOCHASTIC_SCENARIOS,
) -> StochasticScenarioSelection:
    """Grant stochastic authority only to a sufficient effective path set.

    On insufficiency, the returned optimizer scenarios are the caller's existing
    deterministic p25/p50/p75 design scenarios and no weighted risk scenarios
    are exposed to the critic.
    """
    if minimum_effective_scenarios < 1:
        raise ValueError("minimum_effective_scenarios must be at least one.")

    generated = list(generated_scenarios)
    design = list(deterministic_design_scenarios)
    collapsed = _collapse_duplicate_paths(generated)
    # We use the decimal rendering of the existing float contract for this
    # calculation.  This avoids making ten generated ``0.1`` weights fail an
    # exact minimum-of-ten gate solely because binary-float addition rounded
    # the result below ten; it does not introduce a demand-equality tolerance.
    decimal_weights = [Decimal(str(scenario.probability_weight or 0.0)) for scenario in collapsed]
    total_weight = sum(decimal_weights, Decimal())
    if collapsed and total_weight <= 0:
        raise ValueError("Stochastic scenario weights must have a positive total.")
    with localcontext() as context:
        context.prec = 28
        normalized_decimal = [weight / total_weight for weight in decimal_weights] if total_weight else []
        effective_decimal = (
            Decimal(1) / sum((weight * weight for weight in normalized_decimal), Decimal())
            if normalized_decimal else Decimal()
        )
    normalized = [float(weight) for weight in normalized_decimal]
    weighted_paths = [
        scenario.model_copy(update={"probability_weight": weight})
        for scenario, weight in zip(collapsed, normalized, strict=True)
    ]
    effective_count = float(effective_decimal)
    enabled = effective_decimal >= Decimal(minimum_effective_scenarios)
    return StochasticScenarioSelection(
        optimizer_scenarios=weighted_paths if enabled else design,
        risk_scenarios=weighted_paths if enabled else [],
        scenario_count_generated=len(generated),
        unique_scenario_count=len(weighted_paths),
        effective_scenario_count=effective_count,
        stochastic_saa_enabled=enabled,
        fallback_reason=None if enabled else "insufficient_effective_scenarios",
    )
