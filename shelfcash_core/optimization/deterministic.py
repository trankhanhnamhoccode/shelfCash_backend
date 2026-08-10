from __future__ import annotations

import math

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
        holding_cost = profile.holding_penalty + (
            assumption.holding_cost_per_unit_day if assumption else 0
        )
        shortage_cost = profile.shortage_penalty + (
            assumption.shortage_cost_per_unit if assumption else 0
        )
        for day in data.dates:
            inventory_index[(key, day)] = variables.add(
                holding_cost
                + (profile.waste_penalty if day == data.dates[-1] else 0),
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
        },
        warnings=data.warnings,
    )
