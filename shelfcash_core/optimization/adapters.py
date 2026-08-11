from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np

from shelfcash_core.inventory.contracts import PlannedInboundDelivery
from shelfcash_core.optimization.contracts import ProcurementDecisionLine
from shelfcash_core.scenario.lead_time import LeadTimeModel
from shelfcash_core.scenario.shelf_life import ShelfLifeModel
from shelfcash_core.optimization.expiry import resolve_inbound_expiry


def decisions_to_planned_inbound(
    decisions: Sequence[ProcurementDecisionLine],
    *,
    plan_id: str,
    scenario_id: str | None = None,
) -> list[PlannedInboundDelivery]:
    deliveries: list[PlannedInboundDelivery] = []
    suffix = f"-{scenario_id}" if scenario_id else ""
    for index, line in enumerate(decisions):
        expiry = resolve_inbound_expiry(
            arrival_date=line.arrival_date, shelf_life_days=line.shelf_life_days
        )
        deliveries.append(
            PlannedInboundDelivery(
                delivery_id=f"{plan_id}{suffix}-delivery-{index:04d}",
                lot_id=f"{plan_id}{suffix}-lot-{index:04d}",
                purchase_order_id=f"{plan_id}{suffix}-po-{index:04d}",
                source_plan_id=plan_id,
                supplier_id=line.supplier_id,
                store_id=line.store_id,
                ingredient_id=line.ingredient_id,
                quantity=line.order_quantity,
                unit=line.unit,
                arrival_date=line.arrival_date,
                expiry_date=expiry,
                unit_cost=line.unit_price,
                provenance={
                    "realization_type": "optimizer_fixed_lead_time",
                    "expiry_status": "exact" if expiry is not None else "unknown",
                    "expiry_source": "supplier_offer.shelf_life_days" if expiry is not None else "not_configured",
                },
            )
        )
    return deliveries


def decisions_to_scenario_planned_inbound(
    decisions: Sequence[ProcurementDecisionLine],
    scenario_ids: Sequence[str],
    *,
    plan_id: str,
    lead_time_model: LeadTimeModel,
    shelf_life_model: ShelfLifeModel,
    seed: int,
) -> dict[str, list[PlannedInboundDelivery]]:
    """Realize external supply uncertainty before invoking inventory physics."""

    rng = np.random.default_rng(seed)
    output: dict[str, list[PlannedInboundDelivery]] = {}
    for scenario_id in scenario_ids:
        deliveries: list[PlannedInboundDelivery] = []
        for index, line in enumerate(decisions):
            lead_time = lead_time_model.realize(
                order_date=line.order_date,
                supplier_id=line.supplier_id,
                ingredient_id=line.ingredient_id,
                rng=rng,
            )
            official_expiry = resolve_inbound_expiry(
                arrival_date=lead_time.arrival_date, shelf_life_days=line.shelf_life_days
            )
            shelf_life = shelf_life_model.realize(
                official_expiry_date=official_expiry,
                rng=rng,
            )
            arrived_expired = (
                shelf_life.effective_expiry_date is not None
                and shelf_life.effective_expiry_date < lead_time.arrival_date
            )
            deliveries.append(
                PlannedInboundDelivery(
                    delivery_id=f"{plan_id}-{scenario_id}-base-delivery-{index:04d}",
                    lot_id=f"{plan_id}-{scenario_id}-base-lot-{index:04d}",
                    purchase_order_id=f"{plan_id}-{scenario_id}-base-po-{index:04d}",
                    source_plan_id=plan_id,
                    supplier_id=line.supplier_id,
                    store_id=line.store_id,
                    ingredient_id=line.ingredient_id,
                    quantity=line.order_quantity,
                    unit=line.unit,
                    arrival_date=lead_time.arrival_date,
                    expiry_date=shelf_life.effective_expiry_date,
                    unit_cost=line.unit_price,
                    arrival_condition=(
                        "arrived_expired_realization" if arrived_expired else "normal"
                    ),
                    provenance={
                        "realization_type": "external_supply_scenario",
                        "lead_time_source": lead_time.source,
                        "shelf_life_source": shelf_life.source,
                    },
                )
            )
        output[scenario_id] = deliveries
    return output
