from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from shelfcash_core.bom.adapter import validate_sales_product_unit_consistency
from shelfcash_core.bom.contracts import IngredientDemandPackage
from shelfcash_core.bom.engine import propagate_ingredient_demand
from shelfcash_core.data.adapter import adapt_forecast_input
from shelfcash_core.exceptions import RecipeValidationError
from shelfcash_core.pipeline.inference_pipeline import predict_demand
from shelfcash_core.registry.loader import load_artifacts


def predict_ingredient_demand(
    canonical_data: Mapping[str, pd.DataFrame],
    artifact_directory: str | Path,
    cutoff_date: str | pd.Timestamp,
    forecast_horizon: int = 7,
) -> IngredientDemandPackage:
    """Run existing forecast inference, then deterministic Recipe/BOM propagation."""

    if "recipes" not in canonical_data:
        raise RecipeValidationError(
            "canonical_data bắt buộc có recipes cho ingredient demand.",
            details={"missing_key": "recipes"},
        )

    artifacts = load_artifacts(artifact_directory)
    adapted = adapt_forecast_input(canonical_data, artifacts.config)
    validate_sales_product_unit_consistency(
        adapted.sales_history,
        cutoff_date=cutoff_date,
    )

    forecast = predict_demand(
        canonical_data=canonical_data,
        artifact_directory=artifact_directory,
        cutoff_date=cutoff_date,
        forecast_horizon=forecast_horizon,
    )
    return propagate_ingredient_demand(
        forecast=forecast,
        recipes=canonical_data["recipes"],
        unit_conversions=canonical_data.get("unit_conversions"),
    )
