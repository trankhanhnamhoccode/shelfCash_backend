from __future__ import annotations

from typing import Any

from shelfcash_core.inventory.contracts import InventorySimulationPackage
from shelfcash_core.optimization.constraints import validate_plan_constraints
from shelfcash_core.optimization.contracts import (
    CriticResult,
    OptimizationRequest,
    ProcurementPlan,
    StrategyProfile,
)


def _exact_service_floors(
    simulation: InventorySimulationPackage,
) -> tuple[float, float | None, bool]:
    key_fill_rates = [
        item.fill_rate
        for result in simulation.results
        for item in result.summary.by_key
    ]
    minimum_fill = min(key_fill_rates, default=1.0)
    any_design_stockout = any(
        item.shortage_quantity > 1e-8
        for result in simulation.results
        for item in result.summary.by_key
    )
    probability = (
        simulation.risk_metrics.any_stockout_probability
        if simulation.risk_metrics is not None
        else None
    )
    return minimum_fill, probability, any_design_stockout


def _model_mismatch(
    plan: ProcurementPlan,
    simulation: InventorySimulationPackage,
    profile: StrategyProfile,
) -> tuple[bool, dict[str, Any]]:
    predicted_fill = plan.provenance.get("predicted_expected_fill_rate")
    predicted_stockout = plan.provenance.get("predicted_stockout_probability")
    metrics = simulation.risk_metrics
    if predicted_fill is None or predicted_stockout is None or metrics is None:
        return False, {"evaluated": False}
    actual_fill = metrics.mean_key_fill_rate
    actual_stockout = metrics.any_stockout_probability
    actual_shortage_by_key = {
        f"{item.store_id}|{item.ingredient_id}|{item.unit}": item.expected_shortage
        for item in metrics.by_key
    }
    actual_fill_by_key = {
        f"{item.store_id}|{item.ingredient_id}|{item.unit}": item.expected_fill_rate
        for item in metrics.by_key
    }
    predicted_fill_by_key = {
        str(key): float(value)
        for key, value in dict(
            plan.provenance.get("predicted_expected_fill_rate_by_key", {})
        ).items()
    }
    fill_gap = float(predicted_fill) - actual_fill
    key_fill_gaps = (
        {
            key: predicted_fill_by_key[key] - actual_fill_by_key[key]
            for key in actual_fill_by_key
        }
        if set(predicted_fill_by_key) == set(actual_fill_by_key)
        else {}
    )
    stockout_gap = actual_stockout - float(predicted_stockout)
    mismatch = (
        (
            max(key_fill_gaps.values(), default=fill_gap)
            > profile.maximum_fill_rate_model_gap + 1e-9
        )
        or stockout_gap
        > profile.maximum_stockout_probability_model_gap + 1e-9
    )
    return mismatch, {
        "evaluated": True,
        "predicted_fill_rate": float(predicted_fill),
        "simulated_expected_fill_rate": actual_fill,
        "fill_rate_gap": fill_gap,
        "fill_rate_definition": "mean_of_scenario_weighted_inventory_key_fill_rates",
        "predicted_expected_fill_rate_by_key": predicted_fill_by_key,
        "simulated_expected_fill_rate_by_key": actual_fill_by_key,
        "fill_rate_gap_by_key": key_fill_gaps,
        "predicted_stockout_probability": float(predicted_stockout),
        "simulated_stockout_probability": actual_stockout,
        "stockout_probability_gap": stockout_gap,
        "predicted_expected_shortage_by_key": plan.provenance.get(
            "predicted_expected_shortage_by_key", {}
        ),
        "simulated_expected_shortage_by_key": actual_shortage_by_key,
        "predicted_scenario_outcomes": plan.provenance.get("scenario_outcomes", {}),
        "simulated_per_key": [
            item.model_dump(mode="json") for item in metrics.by_key
        ],
    }


def critique_procurement_plan(
    plan: ProcurementPlan,
    request: OptimizationRequest,
    profile: StrategyProfile,
    simulation: InventorySimulationPackage | None,
    *,
    stress_simulation: InventorySimulationPackage | None = None,
    simulation_error: str | None = None,
) -> CriticResult:
    violations, checks = validate_plan_constraints(
        plan,
        request.supplier_offers,
        request.supplier_constraints,
        budget=request.budget,
    )
    warnings = list(plan.warnings)
    details: dict[str, Any] = {}
    if plan.solver_status != "OPTIMAL":
        violations.append(f"SOLVER_STATUS:{plan.solver_status}")
    if request.unknown_constraints:
        violations.extend(
            f"UNKNOWN_CONSTRAINT:{name}" for name in request.unknown_constraints
        )
    selected = [*plan.orders]
    for lines in plan.scenario_recourse_orders.values():
        selected.extend(lines)
    if (
        request.inventory_policy.unknown_expiry == "reject"
        and any(line.shelf_life_days is None for line in selected)
    ):
        violations.append("UNKNOWN_EXPIRY")
    if simulation is None:
        violations.append("M4_SIMULATION_FAILED")
        if simulation_error:
            warnings.append(simulation_error)
        checks.update(
            {
                "m4_accounting": False,
                "capacity": False,
                "service_level": False,
                "risk": False,
                "exact_service_floor": False,
                "candidate_model_match": False,
            }
        )
    else:
        accounting_valid = all(result.accounting_valid for result in simulation.results)
        checks["m4_accounting"] = accounting_valid
        if not accounting_valid:
            violations.append("M4_ACCOUNTING_INVALID")

        capacity_evaluated = all(
            result.provenance.get("capacity_evaluated", False)
            for result in simulation.results
        )
        capacity_valid = all(
            (item.capacity_violation_quantity or 0) <= 1e-9
            for result in simulation.results
            for item in result.summary.by_key
        )
        checks["capacity"] = capacity_valid and capacity_evaluated
        if not capacity_evaluated:
            warnings.append("CAPACITY_NOT_EVALUATED")
        if not capacity_valid:
            violations.append("CAPACITY_CONSEQUENCE")

        metrics = simulation.risk_metrics
        minimum_exact_fill, exact_stockout, design_stockout = _exact_service_floors(
            simulation
        )
        details["exact_simulation"] = {
            "minimum_key_scenario_fill_rate": minimum_exact_fill,
            "any_stockout_probability": exact_stockout,
            "unweighted_design_scenario_stockout_observed": design_stockout,
        }
        floor_valid = (
            minimum_exact_fill + 1e-9
            >= profile.minimum_acceptable_fill_rate
        )
        if exact_stockout is not None:
            floor_valid = floor_valid and (
                exact_stockout
                <= profile.maximum_acceptable_stockout_probability + 1e-9
            )
        checks["exact_service_floor"] = floor_valid
        if not floor_valid:
            violations.append("EXACT_SIMULATION_SAFETY_FLOOR")
            details.setdefault("finding_evidence", {})["EXACT_SIMULATION_SAFETY_FLOOR"] = {
                "minimum_key_scenario_fill_rate": minimum_exact_fill,
                "required_minimum_fill_rate": profile.minimum_acceptable_fill_rate,
                "any_stockout_probability": exact_stockout,
                "maximum_stockout_probability": profile.maximum_acceptable_stockout_probability,
            }

        service_valid = True
        if profile.minimum_expected_fill_rate is not None:
            service_valid = metrics is not None and all(
                item.expected_fill_rate + 1e-9
                >= profile.minimum_expected_fill_rate
                for item in metrics.by_key
            )
        if (
            profile.minimum_fill_rate is not None
            and profile.required_fill_rate_probability is not None
        ):
            if metrics is None:
                service_valid = False
                warnings.append("UNWEIGHTED_SERVICE_PROBABILITY_NOT_EVALUATED")
            else:
                weights = [float(result.probability_weight) for result in simulation.results]
                probabilities_by_key: dict[str, float] = {}
                for key_metric in metrics.by_key:
                    inventory_key = (
                        key_metric.store_id,
                        key_metric.ingredient_id,
                        key_metric.unit,
                    )
                    probability = 0.0
                    for result, weight in zip(
                        simulation.results, weights, strict=True
                    ):
                        summary_by_key = {
                            (item.store_id, item.ingredient_id, item.unit): item
                            for item in result.summary.by_key
                        }
                        if (
                            summary_by_key[inventory_key].fill_rate + 1e-9
                            >= float(profile.minimum_fill_rate)
                        ):
                            probability += weight
                    label = "|".join(inventory_key)
                    probabilities_by_key[label] = probability
                service_valid = service_valid and all(
                    probability + 1e-9
                    >= profile.required_fill_rate_probability
                    for probability in probabilities_by_key.values()
                )
                details["fill_rate_threshold_probability_by_key"] = (
                    probabilities_by_key
                )
        checks["service_level"] = service_valid
        if not service_valid:
            violations.append("SERVICE_LEVEL_REQUIREMENT")
            details.setdefault("finding_evidence", {})["SERVICE_LEVEL_REQUIREMENT"] = {
                "minimum_expected_fill_rate": profile.minimum_expected_fill_rate,
                "minimum_fill_rate": profile.minimum_fill_rate,
                "required_fill_rate_probability": profile.required_fill_rate_probability,
            }

        risk_valid = True
        if profile.maximum_stockout_probability is not None:
            if metrics is None:
                warnings.append("UNWEIGHTED_STOCKOUT_PROBABILITY_NOT_EVALUATED")
                warnings.append("RISK_METRIC_NOT_AVAILABLE")
                checks["risk"] = False
                details["risk_evaluation"] = {
                    "status": "not_evaluated",
                    "reason": "probability_weights_unavailable",
                    "maximum_stockout_probability": profile.maximum_stockout_probability,
                }
            else:
                risk_valid = (
                    metrics.any_stockout_probability
                    <= profile.maximum_stockout_probability + 1e-9
                )
                checks["risk"] = risk_valid
                details["risk_evaluation"] = {
                    "status": "passed" if risk_valid else "violated",
                    "stockout_probability": metrics.any_stockout_probability,
                    "maximum_stockout_probability": profile.maximum_stockout_probability,
                }
                if not risk_valid:
                    violations.append("RISK_CONSTRAINT_VIOLATION")
                    details.setdefault("finding_evidence", {})["RISK_CONSTRAINT_VIOLATION"] = details["risk_evaluation"]
        else:
            checks["risk"] = True

        mismatch, mismatch_details = _model_mismatch(plan, simulation, profile)
        details["candidate_model_mismatch"] = mismatch_details
        checks["candidate_model_match"] = not mismatch
        if mismatch:
            violations.append("CANDIDATE_MODEL_MISMATCH")

    if stress_simulation is not None:
        if not all(result.accounting_valid for result in stress_simulation.results):
            violations.append("STRESS_ACCOUNTING_INVALID")
        if any(
            item.shortage_quantity > 0
            for result in stress_simulation.results
            for item in result.summary.by_key
        ):
            warnings.append("STRESS_SHORTAGE_OBSERVED")
        if any(
            (item.capacity_violation_quantity or 0) > 0
            for result in stress_simulation.results
            for item in result.summary.by_key
        ):
            warnings.append("STRESS_CAPACITY_VIOLATION")
    unique = sorted(set(violations))
    return CriticResult(
        passed=not unique,
        hard_violations=unique,
        warnings=sorted(set(warnings)),
        checks=checks,
        details=details,
    )
