from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta

from shelfcash_core.bom.units import normalize_unit
from shelfcash_core.optimization.contracts import (
    ProcurementPlan,
    SupplierConstraint,
    SupplierOffer,
)
from shelfcash_core.optimization.model_data import supplier_arrival_date
from shelfcash_core.optimization.expiry import resolve_inbound_expiry


def validate_plan_constraints(
    plan: ProcurementPlan,
    offers: Sequence[SupplierOffer],
    supplier_constraints: Sequence[SupplierConstraint] = (),
    *,
    budget: float | None = None,
    tolerance: float = 1e-8,
) -> tuple[list[str], dict[str, bool], dict[str, dict[str, object]]]:
    violations: list[str] = []
    finding_evidence: dict[str, dict[str, object]] = {}
    offer_map = {offer.offer_id: offer for offer in offers}
    all_lines = [*plan.orders]
    for lines in plan.scenario_recourse_orders.values():
        all_lines.extend(lines)
    for line in all_lines:
        offer = offer_map.get(line.offer_id)
        if offer is None:
            violations.append(f"UNKNOWN_OFFER:{line.offer_id}")
            continue
        if (
            line.supplier_id != offer.supplier_id
            or line.store_id != offer.store_id
            or line.ingredient_id != offer.ingredient_id
            or line.emergency != offer.emergency
        ):
            violations.append(f"OFFER_IDENTITY:{line.offer_id}")
        if line.order_date != offer.order_date:
            violations.append(f"ORDER_DATE:{line.offer_id}")
        nominal_arrival = line.order_date + timedelta(days=offer.lead_time_days)
        expected_arrival = supplier_arrival_date(offer)
        if expected_arrival is None:
            code = f"LEAD_TIME:{line.offer_id}"
            violations.append(code)
            finding_evidence[code] = {
                "offer_id": offer.offer_id,
                "order_date": line.order_date.isoformat(),
                "lead_time_days": offer.lead_time_days,
                "nominal_arrival_date": nominal_arrival.isoformat(),
                "available_delivery_days": offer.available_delivery_days,
                "expected_calendar_adjusted_arrival_date": None,
                "actual_arrival_date": line.arrival_date.isoformat(),
                "reason": "delivery_calendar_has_no_available_day",
            }
        elif line.arrival_date != expected_arrival:
            code = f"LEAD_TIME:{line.offer_id}"
            violations.append(code)
            finding_evidence[code] = {
                "offer_id": offer.offer_id,
                "order_date": line.order_date.isoformat(),
                "lead_time_days": offer.lead_time_days,
                "nominal_arrival_date": nominal_arrival.isoformat(),
                "available_delivery_days": offer.available_delivery_days,
                "expected_calendar_adjusted_arrival_date": expected_arrival.isoformat(),
                "actual_arrival_date": line.arrival_date.isoformat(),
                "reason": "arrival_date_does_not_match_supplier_calendar",
            }
        if not math.isclose(line.pack_size, offer.pack_size):
            violations.append(f"PACK_SIZE:{line.offer_id}")
        if not math.isclose(line.unit_price, offer.unit_price):
            violations.append(f"UNIT_PRICE:{line.offer_id}")
        if not math.isclose(line.delivery_cost, offer.delivery_cost):
            violations.append(f"DELIVERY_COST:{line.offer_id}")
        if line.shelf_life_days != offer.shelf_life_days:
            violations.append(f"SHELF_LIFE:{line.offer_id}")
        expected_expiry = None if offer.expiry_tracking_mode == "not_required" else resolve_inbound_expiry(
            arrival_date=line.arrival_date, shelf_life_days=offer.shelf_life_days
        )
        if line.projected_expiry_date != expected_expiry:
            violations.append(f"SHELF_LIFE:{line.offer_id}")
        if line.pack_count != int(line.pack_count):
            violations.append(f"PACK_SIZE_INTEGRALITY:{line.offer_id}")
        if not math.isclose(
            line.order_quantity,
            line.pack_count * offer.pack_size,
            rel_tol=1e-9,
            abs_tol=tolerance,
        ):
            violations.append(f"PACK_SIZE:{line.offer_id}")
        if line.order_quantity + tolerance < offer.minimum_order_quantity:
            violations.append(f"MOQ:{line.offer_id}")
        if (
            offer.maximum_order_quantity is not None
            and line.order_quantity > offer.maximum_order_quantity + tolerance
        ):
            violations.append(f"SUPPLIER_OFFER_MAX:{line.offer_id}")
        if not offer.available:
            violations.append(f"SUPPLIER_UNAVAILABLE:{line.offer_id}")
        if offer.order_cutoff_date is not None and line.order_date > offer.order_cutoff_date:
            violations.append(f"ORDER_CUTOFF:{line.offer_id}")
        if normalize_unit(line.unit) != normalize_unit(offer.unit):
            violations.append(f"UNIT_COMPATIBILITY:{line.offer_id}")
    first_stage_cost = sum(
        line.purchase_cost + line.delivery_cost for line in plan.orders
    )
    if budget is not None and first_stage_cost > budget + tolerance:
        violations.append("BUDGET")
    line_groups = [("FIRST_STAGE", plan.orders)] + [
        (scenario_id, [*plan.orders, *lines])
        for scenario_id, lines in plan.scenario_recourse_orders.items()
    ]
    for constraint in supplier_constraints:
        for group_id, lines in line_groups:
            scoped = [
                line
                for line in lines
                if line.supplier_id == constraint.supplier_id
                and (
                    constraint.ingredient_id is None
                    or line.ingredient_id == constraint.ingredient_id
                )
                and (constraint.store_id is None or line.store_id == constraint.store_id)
            ]
            if (
                constraint.maximum_total_quantity is not None
                and sum(
                    line.order_quantity
                    for line in scoped
                    if normalize_unit(line.unit)
                    == normalize_unit(constraint.unit or "")
                )
                > constraint.maximum_total_quantity + tolerance
            ):
                violations.append(
                    f"SUPPLIER_MAX_QUANTITY:{constraint.supplier_id}:{group_id}"
                )
            if (
                constraint.maximum_total_cost is not None
                and sum(line.purchase_cost + line.delivery_cost for line in scoped)
                > constraint.maximum_total_cost + tolerance
            ):
                violations.append(
                    f"SUPPLIER_MAX_COST:{constraint.supplier_id}:{group_id}"
                )
    unique = sorted(set(violations))
    return unique, {
        "offers": not any(item.startswith("UNKNOWN_OFFER") for item in unique),
        "pack_size": not any("PACK_SIZE" in item for item in unique),
        "moq": not any(item.startswith("MOQ") for item in unique),
        "budget": "BUDGET" not in unique,
        "supplier_capacity": not any(
            item.startswith("SUPPLIER_MAX") for item in unique
        ),
        "availability": not any(
            "UNAVAILABLE" in item or "CUTOFF" in item for item in unique
        ),
        "unit_compatibility": not any(
            item.startswith("UNIT_COMPATIBILITY") for item in unique
        ),
        "lead_time": not any(item.startswith("LEAD_TIME") for item in unique),
    }, finding_evidence
