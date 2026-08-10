"""Public APIs for ShelfCash prediction, consequence, and action engines."""

from shelfcash_core.bom.contracts import IngredientDemandPackage
from shelfcash_core.bom.engine import propagate_ingredient_demand
from shelfcash_core.config import ForecastConfig
from shelfcash_core.contracts import ForecastPackage, TrainingResult
from shelfcash_core.inventory.contracts import (
    InventoryDemandScenario,
    InventoryLot,
    InventorySimulationPackage,
    PlannedInboundDelivery,
)
from shelfcash_core.inventory.simulator import (
    simulate_inventory,
    simulate_inventory_scenarios,
    simulate_quantile_inventory,
)
from shelfcash_core.optimization.contracts import (
    OptimizationRequest,
    OptimizationResult,
)
from shelfcash_core.optimization.optimizer import optimize_procurement
from shelfcash_core.pipeline.inference_pipeline import predict_demand
from shelfcash_core.pipeline.ingredient_demand_pipeline import (
    predict_ingredient_demand,
)
from shelfcash_core.pipeline.scenario_pipeline import (
    predict_ingredient_demand_scenarios,
)
from shelfcash_core.pipeline.training_pipeline import train_forecast_core
from shelfcash_core.scenario.bom import propagate_ingredient_demand_scenarios
from shelfcash_core.scenario.composer import generate_product_demand_scenarios
from shelfcash_core.scenario.contracts import IngredientDemandScenarioBundle

__all__ = [
    "ForecastConfig",
    "ForecastPackage",
    "IngredientDemandPackage",
    "IngredientDemandScenarioBundle",
    "InventoryDemandScenario",
    "InventoryLot",
    "InventorySimulationPackage",
    "OptimizationRequest",
    "OptimizationResult",
    "PlannedInboundDelivery",
    "TrainingResult",
    "generate_product_demand_scenarios",
    "optimize_procurement",
    "predict_demand",
    "predict_ingredient_demand",
    "predict_ingredient_demand_scenarios",
    "propagate_ingredient_demand",
    "propagate_ingredient_demand_scenarios",
    "simulate_inventory",
    "simulate_inventory_scenarios",
    "simulate_quantile_inventory",
    "train_forecast_core",
]
