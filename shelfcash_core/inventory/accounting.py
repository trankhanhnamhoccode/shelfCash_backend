from __future__ import annotations

import math

from shelfcash_core.exceptions import InventoryAccountingError
from shelfcash_core.inventory.contracts import DailyInventoryLedger


def validate_accounting_ledger(
    ledger: DailyInventoryLedger,
    *,
    tolerance: float,
) -> None:
    expected = (
        ledger.beginning_quantity
        + ledger.inbound_quantity
        - ledger.fulfilled_quantity
        - ledger.expired_quantity
        - ledger.waste_quantity
    )
    if not math.isclose(
        expected,
        ledger.ending_quantity,
        rel_tol=tolerance,
        abs_tol=tolerance,
    ):
        raise InventoryAccountingError(
            "Inventory accounting invariant bị vi phạm.",
            details={
                "scenario_id": ledger.scenario_id,
                "simulation_date": ledger.simulation_date.isoformat(),
                "store_id": ledger.store_id,
                "ingredient_id": ledger.ingredient_id,
                "expected_ending": expected,
                "actual_ending": ledger.ending_quantity,
                "ledger": ledger.model_dump(mode="json"),
            },
        )
