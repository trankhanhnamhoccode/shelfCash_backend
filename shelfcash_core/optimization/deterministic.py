from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from shelfcash_core.bom.units import normalize_unit
from shelfcash_core.exceptions import OptimizationError
from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    ProcurementDecisionLine,
    ProcurementPlan,
    StrategyProfile,
)
from shelfcash_core.optimization.model_data import (
    EligibleOffer,
    build_problem_data,
    expected_daily_demand,
    shortage_cost_per_target_unit,
)


class _Variables:
    def __init__(self) -> None:
        self.cost: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.integrality: list[int] = []

    def add(self, cost: float, lower: float, upper: float, integer: bool = False) -> int:
        index = len(self.cost)
        self.cost.append(cost)
        self.lower.append(lower)
        self.upper.append(upper)
        self.integrality.append(int(integer))
        return index


def _offer_upper_packs(
    item: EligibleOffer,
    request: OptimizationRequest,
    maximum_demand: dict[tuple[str, str], float],
) -> int:
    offer = item.offer
    if offer.maximum_order_quantity is not None:
        return math.floor(offer.maximum_order_quantity / offer.pack_size + 1e-12)
    key = (offer.store_id, offer.ingredient_id)
    target_quantity = maximum_demand.get(key, 0) / item.factor_to_target
    safe_quantity = max(target_quantity, offer.minimum_order_quantity, offer.pack_size)
    if request.budget is not None and offer.unit_price > 0:
        safe_quantity = min(safe_quantity, request.budget / offer.unit_price)
    return max(0, math.ceil(safe_quantity / offer.pack_size - 1e-12))


def _decision(item: EligibleOffer, packs: int) -> ProcurementDecisionLine:
    offer = item.offer
    quantity = packs * offer.pack_size
    return ProcurementDecisionLine(
        offer_id=offer.offer_id,
        supplier_id=offer.supplier_id,
        store_id=offer.store_id,
        ingredient_id=offer.ingredient_id,
        unit=normalize_unit(offer.unit),
        order_date=offer.order_date,
        arrival_date=item.arrival_date,
        pack_count=packs,
        pack_size=offer.pack_size,
        order_quantity=quantity,
        unit_price=offer.unit_price,
        purchase_cost=quantity * offer.unit_price,
        delivery_cost=offer.delivery_cost,
        shelf_life_days=offer.shelf_life_days,
        emergency=offer.emergency,
    )


def _no_order_diagnostics(
    data,
    request: OptimizationRequest,
    daily_demand: dict,
    orders: list[ProcurementDecisionLine],
    profile: StrategyProfile,
) -> list[dict]:
    """Return compact, deterministic explanations for demand keys with no buy."""

    selected_by_key: dict[tuple[str, str], float] = {}
    for line in orders:
        key = (line.store_id, line.ingredient_id)
        selected_by_key[key] = selected_by_key.get(key, 0.0) + line.order_quantity
    diagnostics = []
    for key in data.keys:
        demand_by_date = {
            day.isoformat(): daily_demand[(key, day)]
            for day in data.dates
            if daily_demand[(key, day)] > 0
        }
        if not demand_by_date or selected_by_key.get(key, 0.0) > 1e-9:
            continue
        balance = data.initial_quantity.get(key, 0.0)
        first_shortage_date = None
        for day in data.dates:
            balance += data.existing_inbound.get((key, day), 0.0)
            balance -= daily_demand[(key, day)]
            if balance < -1e-9 and first_shortage_date is None:
                first_shortage_date = day.isoformat()
        eligible = [
            item for item in data.regular_offers
            if (item.offer.store_id, item.offer.ingredient_id) == key
        ]
        matching = [
            offer for offer in request.supplier_offers
            if (offer.store_id, offer.ingredient_id) == key
        ]
        if first_shortage_date is None:
            reason = "NO_PURCHASE_INVENTORY_SUFFICIENT"
        elif not matching:
            reason = "NO_PURCHASE_NO_VALID_SUPPLIER"
        elif not eligible:
            arrivals = [
                offer.order_date + timedelta(days=offer.lead_time_days)
                for offer in matching
            ]
            reason = (
                "NO_PURCHASE_ARRIVES_TOO_LATE"
                if arrivals and min(arrivals) > request.planning_end_date
                else "NO_PURCHASE_NO_VALID_SUPPLIER"
            )
        elif request.budget is not None and all(
            max(item.offer.minimum_order_quantity, item.offer.pack_size)
            * item.offer.unit_price + item.offer.delivery_cost > request.budget + 1e-9
            for item in eligible
        ):
            reason = "NO_PURCHASE_BUDGET_BLOCKED"
        elif shortage_cost_per_target_unit(data, key)[0] <= 0:
            reason = "NO_PURCHASE_SHORTAGE_CONSEQUENCE_NOT_CONFIGURED"
        else:
            reason = "NO_PURCHASE_OBJECTIVE_PREFERS_SHORTAGE"
        base_shortage_cost, shortage_cost_source = shortage_cost_per_target_unit(data, key)
        shortage_unit_cost = base_shortage_cost * profile.shortage_penalty
        baseline_balance = data.initial_quantity.get(key, 0.0)
        baseline_shortage = 0.0
        for day in data.dates:
            baseline_balance += data.existing_inbound.get((key, day), 0.0)
            baseline_balance -= daily_demand[(key, day)]
            if baseline_balance < 0:
                baseline_shortage += -baseline_balance
                baseline_balance = 0.0
        candidate = None
        if eligible:
            item = min(
                eligible,
                key=lambda value: (
                    max(value.offer.minimum_order_quantity, value.offer.pack_size)
                    * value.offer.unit_price + value.offer.delivery_cost,
                    value.arrival_date,
                    value.offer.offer_id,
                ),
            )
            packs = math.ceil(
                max(item.offer.minimum_order_quantity, item.offer.pack_size)
                / item.offer.pack_size - 1e-12
            )
            candidate = {
                "quantity": packs * item.pack_quantity_target,
                "quantity_unit": data.target_units[key],
                "arrival_date": item.arrival_date.isoformat(),
                "cost": packs * item.offer.pack_size * item.offer.unit_price + item.offer.delivery_cost,
                "objective_purchase_cost": (packs * item.offer.pack_size * item.offer.unit_price + item.offer.delivery_cost) * (1 + profile.cash_penalty),
            }
        diagnostics.append(
            {
                "ingredient_id": key[1],
                "unit": data.target_units[key],
                "demand_by_date": demand_by_date,
                "usable_initial_inventory": data.initial_quantity.get(key, 0.0),
                "open_inbound_by_date": {
                    day.isoformat(): data.existing_inbound.get((key, day), 0.0)
                    for day in data.dates
                    if data.existing_inbound.get((key, day), 0.0) > 0
                },
                "first_shortage_date_without_purchase": first_shortage_date,
                "budget_limit": request.budget,
                "no_order_reason": reason,
                "no_purchase_consequence": {
                    "shortage_quantity": baseline_shortage,
                    "quantity_unit": data.target_units[key],
                    "shortage_cost_per_unit": shortage_unit_cost,
                    "base_cost_source": shortage_cost_source,
                    "shortage_cost": baseline_shortage * shortage_unit_cost,
                },
                "purchase_candidate": candidate,
                "strategy_multiplier": profile.shortage_penalty,
                "comparison": (
                    "configured_shortage_consequence_missing"
                    if shortage_unit_cost <= 0
                    else "minimum_feasible_pack_vs_chronological_no_purchase_shortage"
                ),
                "offers": [
                    {
                        "supplier_id": item.offer.supplier_id,
                        "offer_id": item.offer.offer_id,
                        "available": item.offer.available,
                        "unit": item.offer.unit,
                        "unit_conversion_factor": item.factor_to_target,
                        "moq": item.offer.minimum_order_quantity,
                        "pack_size": item.offer.pack_size,
                        "maximum_order_quantity": item.offer.maximum_order_quantity,
                        "lead_time_days": item.offer.lead_time_days,
                        "order_date": item.offer.order_date.isoformat(),
                        "arrival_date": item.arrival_date.isoformat(),
                        "unit_purchase_price": item.offer.unit_price,
                    }
                    for item in eligible
                ],
            }
        )
    return diagnostics


def solve_deterministic_procurement(
    request: OptimizationRequest,
    profile: StrategyProfile,
) -> ProcurementPlan:
    """Generate a candidate with a chronological aggregate-inventory MILP."""

    data = build_problem_data(request)
    daily_demand = expected_daily_demand(data)
    maximum_demand = {
        key: max(
            sum(data.demand.get((scenario_id, key, day), 0) for day in data.dates)
            for scenario_id in data.scenario_ids
        )
        for key in data.keys
    }
    variables = _Variables()
    x_index: dict[int, int] = {}
    y_index: dict[int, int] = {}
    upper_packs: dict[int, int] = {}
    for offer_index, item in enumerate(data.regular_offers):
        upper = _offer_upper_packs(item, request, maximum_demand)
        upper_packs[offer_index] = upper
        purchase_per_pack = item.offer.pack_size * item.offer.unit_price
        x_index[offer_index] = variables.add(
            purchase_per_pack * (1 + profile.cash_penalty), 0, upper, True
        )
        y_index[offer_index] = variables.add(
            item.offer.delivery_cost * (1 + profile.cash_penalty), 0, 1, True
        )

    inventory_index: dict[tuple[tuple[str, str], object], int] = {}
    shortage_index: dict[tuple[tuple[str, str], object], int] = {}
    for key in data.keys:
        assumption = data.assumptions.get(key)
        holding_cost = profile.holding_penalty * (
            assumption.holding_cost_per_unit_day if assumption else 0
        )
        shortage_cost = profile.shortage_penalty * shortage_cost_per_target_unit(data, key)[0]
        for day in data.dates:
            inventory_index[(key, day)] = variables.add(
                holding_cost,
                0,
                assumption.capacity_quantity
                if assumption and assumption.capacity_quantity is not None
                else np.inf,
            )
            shortage_index[(key, day)] = variables.add(shortage_cost, 0, np.inf)

    rows: list[dict[int, float]] = []
    lower: list[float] = []
    upper: list[float] = []

    def add_constraint(coefficients: dict[int, float], low: float, high: float) -> None:
        rows.append(coefficients)
        lower.append(low)
        upper.append(high)

    for offer_index, item in enumerate(data.regular_offers):
        x = x_index[offer_index]
        y = y_index[offer_index]
        maximum = upper_packs[offer_index]
        minimum = math.ceil(
            item.offer.minimum_order_quantity / item.offer.pack_size - 1e-12
        )
        add_constraint({x: 1, y: -maximum}, -np.inf, 0)
        add_constraint({x: -1, y: minimum}, -np.inf, 0)

    for key in data.keys:
        previous: int | None = None
        for day in data.dates:
            coefficients = {
                inventory_index[(key, day)]: 1,
                shortage_index[(key, day)]: -1,
            }
            if previous is not None:
                coefficients[previous] = -1
                starting = 0.0
            else:
                starting = data.initial_quantity.get(key, 0)
            for offer_index, item in enumerate(data.regular_offers):
                if (
                    (item.offer.store_id, item.offer.ingredient_id) == key
                    and item.arrival_date == day
                ):
                    coefficients[x_index[offer_index]] = -item.pack_quantity_target
            rhs = (
                starting
                + data.existing_inbound.get((key, day), 0)
                - daily_demand[(key, day)]
            )
            add_constraint(coefficients, rhs, rhs)
            previous = inventory_index[(key, day)]

    # ``minimum_expected_fill_rate`` is an explicit strategy contract.  In
    # deterministic mode daily demand is already the design-weighted demand,
    # so the corresponding aggregate fill floor can be enforced directly.
    # This intentionally does not turn critic-only safety floors into MILP
    # constraints.
    if profile.minimum_expected_fill_rate is not None:
        for key in data.keys:
            total_demand = sum(daily_demand[(key, day)] for day in data.dates)
            add_constraint(
                {shortage_index[(key, day)]: 1 for day in data.dates},
                -np.inf,
                total_demand * (1 - profile.minimum_expected_fill_rate),
            )

    if request.budget is not None:
        coefficients: dict[int, float] = {}
        for index, item in enumerate(data.regular_offers):
            coefficients[x_index[index]] = item.offer.pack_size * item.offer.unit_price
            coefficients[y_index[index]] = item.offer.delivery_cost
        add_constraint(coefficients, -np.inf, request.budget)

    for constraint in request.supplier_constraints:
        matching = [
            (index, item)
            for index, item in enumerate(data.regular_offers)
            if item.offer.supplier_id == constraint.supplier_id
            and (constraint.store_id is None or item.offer.store_id == constraint.store_id)
            and (
                constraint.ingredient_id is None
                or item.offer.ingredient_id == constraint.ingredient_id
            )
        ]
        if constraint.maximum_total_quantity is not None:
            if any(
                normalize_unit(item.offer.unit) != normalize_unit(constraint.unit or "")
                for _, item in matching
            ):
                raise OptimizationError(
                    "Supplier quantity cap unit does not match scoped offers.",
                    code="INVALID_PROCUREMENT_UNIT",
                )
            add_constraint(
                {
                    x_index[index]: item.offer.pack_size
                    for index, item in matching
                },
                -np.inf,
                constraint.maximum_total_quantity,
            )
        if constraint.maximum_total_cost is not None:
            coefficients = {}
            for index, item in matching:
                coefficients[x_index[index]] = item.offer.pack_size * item.offer.unit_price
                coefficients[y_index[index]] = item.offer.delivery_cost
            add_constraint(coefficients, -np.inf, constraint.maximum_total_cost)

    matrix = np.zeros((len(rows), len(variables.cost)))
    for row_index, coefficients in enumerate(rows):
        for column, value in coefficients.items():
            matrix[row_index, column] = value
    result = milp(
        c=np.asarray(variables.cost),
        integrality=np.asarray(variables.integrality),
        bounds=Bounds(variables.lower, variables.upper),
        constraints=LinearConstraint(matrix, lower, upper),
        options={"time_limit": 30},
    )
    statuses = {0: "OPTIMAL", 1: "LIMIT_REACHED", 2: "INFEASIBLE", 3: "UNBOUNDED"}
    status = statuses.get(result.status, "SOLVER_ERROR")
    orders: list[ProcurementDecisionLine] = []
    if result.x is not None and result.status in {0, 1}:
        for index, item in enumerate(data.regular_offers):
            packs = round(result.x[x_index[index]])
            if packs > 0:
                orders.append(_decision(item, packs))
    purchase_cost = sum(line.purchase_cost + line.delivery_cost for line in orders)
    weighted_first_stage_cost = sum(
        line.purchase_cost * (1 + profile.cash_penalty)
        + line.delivery_cost * (1 + profile.cash_penalty)
        for line in orders
    )
    objective_value = float(result.fun) if result.fun is not None else None
    holding_term = (
        sum(float(result.x[index]) * variables.cost[index] for index in inventory_index.values())
        if result.x is not None else None
    )
    objective_breakdown = {
        "cost_unit": "supplier_offer_unit_price_currency_or_cost_unit",
        "purchase_term": weighted_first_stage_cost,
        "shortage_term": (
            sum(
                float(result.x[index]) * variables.cost[index]
                for index in shortage_index.values()
            )
            if result.x is not None
            else None
        ),
        "holding_term": holding_term,
        "waste_term": 0.0,
        "total_objective": objective_value,
    }
    return ProcurementPlan(
        plan_id=f"{request.request_id}-{profile.name.lower()}",
        strategy=profile.name,
        orders=orders,
        purchase_cost=purchase_cost,
        expected_recourse_cost=(
            max(0.0, objective_value - weighted_first_stage_cost)
            if objective_value is not None
            else 0
        ),
        objective_value=objective_value,
        solver_status=status,
        completed=False,
        provenance={
            "solver": "scipy.optimize.milp",
            "formulation": "chronological_aggregate_inventory_mip_v1",
            "exact_inventory_physics": False,
            "requires_m4_resimulation": True,
            "deterministic_service_constraint": (
                "aggregate_design_fill_rate"
                if profile.minimum_expected_fill_rate is not None
                else None
            ),
            "objective_breakdown": objective_breakdown,
            "no_order_diagnostics": _no_order_diagnostics(
                data, request, daily_demand, orders, profile
            ),
        },
        warnings=data.warnings,
    )
