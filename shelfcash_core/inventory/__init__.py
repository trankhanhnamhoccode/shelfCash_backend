"""Lot-level inventory consequence engine."""

from shelfcash_core.inventory.contracts import (
    InboundDelivery,
    InventoryDemandScenario,
    InventoryKeyRiskMetrics,
    InventoryKeySummary,
    InventoryLot,
    InventorySimulationPackage,
    InventorySimulationPolicy,
    InventorySimulationResult,
    LotExpiryTrace,
    PlannedInboundDelivery,
)
from shelfcash_core.inventory.monte_carlo import MonteCarloInventoryRunner
from shelfcash_core.inventory.simulator import (
    simulate_inventory,
    simulate_inventory_scenarios,
    simulate_quantile_inventory,
)

__all__ = [
    "InboundDelivery",
    "InventoryDemandScenario",
    "InventoryKeyRiskMetrics",
    "InventoryKeySummary",
    "InventoryLot",
    "InventorySimulationPackage",
    "InventorySimulationPolicy",
    "InventorySimulationResult",
    "LotExpiryTrace",
    "MonteCarloInventoryRunner",
    "PlannedInboundDelivery",
    "simulate_inventory",
    "simulate_inventory_scenarios",
    "simulate_quantile_inventory",
]
