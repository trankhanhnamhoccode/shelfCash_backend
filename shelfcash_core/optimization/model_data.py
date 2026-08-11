from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from shelfcash_core.bom.units import UnitConverter, normalize_unit
from shelfcash_core.exceptions import BOMError, OptimizationError
from shelfcash_core.inventory.adapters import normalize_cost_assumptions
from shelfcash_core.inventory.contracts import ConsequenceCostAssumption
from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    SupplierOffer,
)
from shelfcash_core.optimization.expiry import resolve_inbound_expiry

InventoryKey = tuple[str, str]


@dataclass(frozen=True)
class EligibleOffer:
    offer: SupplierOffer
    arrival_date: date
    factor_to_target: float
    pack_quantity_target: float
    expiry_date: date | None


@dataclass(frozen=True)
class InitialExpiryBucket:
    key: InventoryKey
    expiry_date: date
    quantity: float


@dataclass(frozen=True)
class ExistingInboundExpiryBucket:
    key: InventoryKey
    arrival_date: date
    expiry_date: date
    quantity: float


@dataclass
class OptimizationProblemData:
    keys: list[InventoryKey]
    dates: list[date]
    target_units: dict[InventoryKey, str]
    scenario_ids: list[str]
    scenario_weights: np.ndarray
    probabilistic_weights: bool
    demand: dict[tuple[str, InventoryKey, date], float]
    initial_quantity: dict[InventoryKey, float]
    initial_expiry_buckets: list[InitialExpiryBucket]
    existing_inbound: dict[tuple[InventoryKey, date], float]
    existing_inbound_expiry_buckets: list[ExistingInboundExpiryBucket]
    regular_offers: list[EligibleOffer]
    emergency_offers: list[EligibleOffer]
    assumptions: dict[InventoryKey, ConsequenceCostAssumption]
    warnings: list[str]


def shortage_cost_per_target_unit(
    data: OptimizationProblemData, key: InventoryKey
) -> tuple[float, str]:
    """Return the base shortage consequence in the target-unit cost currency.

    A configured consequence is authoritative.  In its absence, a timely
    supplier's unit price is an explicit, conservative replacement-cost
    fallback: it is monetary, conversion-aware, and does not fabricate a
    product margin or charge a lost sale once per ingredient.  Delivery cost
    intentionally remains pack-level in the purchase term.
    """
    assumption = data.assumptions.get(key)
    if assumption is not None and assumption.shortage_cost_per_unit > 0:
        return assumption.shortage_cost_per_unit, "configured_shortage_consequence"
    prices = [
        item.offer.unit_price / item.factor_to_target
        for item in data.regular_offers
        if (item.offer.store_id, item.offer.ingredient_id) == key
    ]
    if prices:
        return min(prices), "supplier_replacement_cost_fallback"
    return 0.0, "not_configured"


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def supplier_arrival_date(offer: SupplierOffer) -> date | None:
    """Return the canonical calendar-adjusted arrival used by solver and Critic.

    An unrestricted offer arrives at its nominal lead-time date.  A configured
    weekday calendar shifts that nominal date to its first allowed weekday;
    an empty calendar has no valid arrival.
    """
    nominal = offer.order_date + timedelta(days=offer.lead_time_days)
    if offer.available_delivery_days is None:
        return nominal
    allowed = set(offer.available_delivery_days)
    if not allowed:
        return None
    for offset in range(7):
        candidate = nominal + timedelta(days=offset)
        if candidate.weekday() in allowed:
            return candidate
    return None


def build_problem_data(request: OptimizationRequest) -> OptimizationProblemData:
    if not request.demand_scenarios:
        raise OptimizationError("At least one demand scenario is required.")
    try:
        converter = UnitConverter(request.unit_conversions)
    except BOMError as exc:
        raise OptimizationError(
            "Procurement unit-conversion metadata is invalid.",
            code="INVALID_PROCUREMENT_UNIT",
            details=exc.details,
        ) from exc
    target_units: dict[InventoryKey, str] = {}
    demand: dict[tuple[str, InventoryKey, date], float] = defaultdict(float)
    for scenario in request.demand_scenarios:
        for line in scenario.lines:
            if not request.decision_date <= line.target_date <= request.planning_end_date:
                continue
            key = (line.store_id, line.ingredient_id)
            normalized = normalize_unit(line.unit)
            current = target_units.get(key)
            if current is not None and current != normalized:
                raise OptimizationError(
                    "Demand units are inconsistent for one store-ingredient.",
                    details={"key": key, "units": sorted({current, normalized})},
                )
            target_units[key] = normalized
            demand[(scenario.scenario_id, key, line.target_date)] += line.quantity
    if not target_units:
        raise OptimizationError("No demand falls within the planning horizon.")

    warnings: set[str] = set()

    def factor(key: InventoryKey, unit: str) -> float:
        try:
            return converter.conversion_factor(key[1], unit, target_units[key])
        except BOMError as exc:
            raise OptimizationError(
                "Procurement inputs contain incompatible units.",
                code="INVALID_PROCUREMENT_UNIT",
                details={"key": key, **exc.details},
            ) from exc

    initial_quantity: defaultdict[InventoryKey, float] = defaultdict(float)
    expiry_bucket_quantities: defaultdict[tuple[InventoryKey, date], float] = defaultdict(float)
    for lot in request.initial_inventory:
        key = (lot.store_id, lot.ingredient_id)
        if key not in target_units:
            continue
        expired_at_start = lot.expiry_date is not None and (
            lot.expiry_date < request.decision_date
            if request.inventory_policy.expiry_inclusive
            else lot.expiry_date <= request.decision_date
        )
        if expired_at_start:
            warnings.add("AGGREGATE_MODEL_EXCLUDED_PRESTART_EXPIRED_LOT")
            continue
        if lot.expiry_date is None:
            warnings.add("AGGREGATE_MODEL_COUNTS_UNKNOWN_EXPIRY_LOT")
        quantity = lot.quantity_remaining * factor(key, lot.unit)
        initial_quantity[key] += quantity
        if lot.expiry_date is not None:
            expiry_bucket_quantities[(key, lot.expiry_date)] += quantity

    existing_inbound: defaultdict[tuple[InventoryKey, date], float] = defaultdict(float)
    existing_inbound_expiry_buckets: list[ExistingInboundExpiryBucket] = []
    for delivery in request.existing_inbound:
        key = (delivery.store_id, delivery.ingredient_id)
        if key in target_units and request.decision_date <= delivery.arrival_date <= request.planning_end_date:
            quantity = delivery.quantity * factor(key, delivery.unit)
            existing_inbound[(key, delivery.arrival_date)] += quantity
            if delivery.expiry_date is None:
                warnings.add("INBOUND_EXPIRY_NOT_EVALUATED")
            else:
                existing_inbound_expiry_buckets.append(ExistingInboundExpiryBucket(
                    key=key, arrival_date=delivery.arrival_date,
                    expiry_date=delivery.expiry_date, quantity=quantity,
                ))

    eligible: list[EligibleOffer] = []
    for offer in request.supplier_offers:
        key = (offer.store_id, offer.ingredient_id)
        arrival = supplier_arrival_date(offer)
        if key not in target_units:
            continue
        if (
            not offer.available
            or offer.order_date < request.decision_date
            or offer.order_date > request.planning_end_date
            or arrival is None
            or arrival > request.planning_end_date
            or (
                offer.order_cutoff_date is not None
                and offer.order_date > offer.order_cutoff_date
            )
        ):
            continue
        conversion = factor(key, offer.unit)
        eligible.append(
            EligibleOffer(
                offer=offer,
                arrival_date=arrival,
                factor_to_target=conversion,
                pack_quantity_target=offer.pack_size * conversion,
                expiry_date=resolve_inbound_expiry(
                    arrival_date=arrival, shelf_life_days=offer.shelf_life_days
                ),
            )
        )
        if offer.shelf_life_days is None:
            warnings.add("PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED")

    raw_weights = [scenario.probability_weight for scenario in request.demand_scenarios]
    probabilistic = all(weight is not None for weight in raw_weights)
    if probabilistic:
        weights = np.asarray(raw_weights, dtype=float)
        weights /= weights.sum()
    else:
        weights = np.full(len(raw_weights), 1 / len(raw_weights))
        warnings.add("UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS")

    try:
        normalized_assumptions = normalize_cost_assumptions(
            request.cost_assumptions, target_units, converter
        )
    except BOMError as exc:
        raise OptimizationError(
            "Procurement cost assumptions contain incompatible units.",
            code="INVALID_PROCUREMENT_UNIT",
            details=exc.details,
        ) from exc
    assumptions = {
        (item.store_id, item.ingredient_id): item for item in normalized_assumptions
    }
    if len(assumptions) != len(normalized_assumptions):
        raise OptimizationError("Duplicate consequence cost assumptions.")
    for key in target_units:
        assumption = assumptions.get(key)
        if assumption is None or assumption.shortage_cost_per_unit <= 0:
            if any((item.offer.store_id, item.offer.ingredient_id) == key for item in eligible):
                warnings.add("SHORTAGE_COST_FALLBACK_USED")
            else:
                warnings.add("SHORTAGE_CONSEQUENCE_NOT_CONFIGURED")

    return OptimizationProblemData(
        keys=sorted(target_units),
        dates=_date_range(request.decision_date, request.planning_end_date),
        target_units=target_units,
        scenario_ids=[scenario.scenario_id for scenario in request.demand_scenarios],
        scenario_weights=weights,
        probabilistic_weights=probabilistic,
        demand=dict(demand),
        initial_quantity=dict(initial_quantity),
        initial_expiry_buckets=[
            InitialExpiryBucket(key=key, expiry_date=expiry_date, quantity=quantity)
            for (key, expiry_date), quantity in sorted(expiry_bucket_quantities.items())
        ],
        existing_inbound=dict(existing_inbound),
        existing_inbound_expiry_buckets=existing_inbound_expiry_buckets,
        regular_offers=[item for item in eligible if not item.offer.emergency],
        emergency_offers=[item for item in eligible if item.offer.emergency],
        assumptions=assumptions,
        warnings=sorted(warnings),
    )


def expected_daily_demand(
    data: OptimizationProblemData,
) -> dict[tuple[InventoryKey, date], float]:
    output: dict[tuple[InventoryKey, date], float] = {}
    for key in data.keys:
        for day in data.dates:
            output[(key, day)] = sum(
                float(weight) * data.demand.get((scenario_id, key, day), 0)
                for scenario_id, weight in zip(
                    data.scenario_ids, data.scenario_weights, strict=True
                )
            )
    return output
