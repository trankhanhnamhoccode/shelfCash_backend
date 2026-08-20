from __future__ import annotations

from datetime import date

from shelfcash_core.exceptions import UnknownExpiryError
from shelfcash_core.inventory.contracts import (
    FEFOResult,
    InventoryLot,
    InventorySimulationPolicy,
    LotConsumptionTrace,
)


def is_expired(
    lot: InventoryLot,
    simulation_date: date,
    policy: InventorySimulationPolicy,
) -> bool:
    if lot.expiry_date is None:
        if policy.unknown_expiry == "reject":
            raise UnknownExpiryError(
                f"Lot {lot.lot_id} thiếu expiry_date.",
                details={"lot_id": lot.lot_id},
            )
        return False
    if policy.expiry_inclusive:
        return lot.expiry_date < simulation_date
    return lot.expiry_date <= simulation_date


def fefo_sort_key(lot: InventoryLot) -> tuple[date, int, date, str]:
    """Expiry is authoritative; unknown receipt time has no invented age.

    For equal expiry dates, known receipt dates sort chronologically first.
    Unknown receipt dates form a separate deterministic group ordered only by
    stable lot ID, rather than being treated as oldest or newest.
    """
    if lot.received_date is None:
        return (lot.expiry_date or date.max, 1, date.max, lot.lot_id)
    return (lot.expiry_date or date.max, 0, lot.received_date, lot.lot_id)


def consume_fefo(
    lots: list[InventoryLot],
    demand_quantity: float,
    *,
    scenario_id: str,
    simulation_date: date,
    policy: InventorySimulationPolicy,
) -> FEFOResult:
    """Consume usable lots deterministically without changing the input list."""

    remaining_demand = float(demand_quantity)
    updated: list[InventoryLot] = []
    traces: list[LotConsumptionTrace] = []
    warnings: set[str] = set()
    for lot in sorted(lots, key=fefo_sort_key):
        if lot.expiry_date is None:
            if policy.unknown_expiry == "reject":
                raise UnknownExpiryError(
                    f"Lot {lot.lot_id} thiếu expiry_date.",
                    details={"lot_id": lot.lot_id},
                )
            warnings.add("UNKNOWN_EXPIRY_PLACED_LAST")
        if is_expired(lot, simulation_date, policy):
            updated.append(lot.model_copy(deep=True))
            continue
        consumed = min(lot.quantity_remaining, remaining_demand)
        new_quantity = lot.quantity_remaining - consumed
        if consumed > 0:
            traces.append(
                LotConsumptionTrace(
                    scenario_id=scenario_id,
                    simulation_date=simulation_date,
                    store_id=lot.store_id,
                    ingredient_id=lot.ingredient_id,
                    lot_id=lot.lot_id,
                    quantity=consumed,
                    unit=lot.unit,
                    lot_expiry_date=lot.expiry_date,
                )
            )
            remaining_demand -= consumed
        updated.append(lot.model_copy(update={"quantity_remaining": new_quantity}))

    fulfilled = demand_quantity - remaining_demand
    return FEFOResult(
        updated_lots=updated,
        fulfilled_quantity=fulfilled,
        shortage_quantity=remaining_demand,
        traces=traces,
        warnings=sorted(warnings),
    )
