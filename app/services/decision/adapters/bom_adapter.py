"""Adapter from the canonical ORM recipe/forecast state to ShelfCash Core BOM."""

from __future__ import annotations

import json

import pandas as pd

from app.core.exceptions import PlanningError
from app.models.business import IngredientModel
from app.repositories.recipes import RecipeRepository
from shelfcash_core.bom.engine import propagate_ingredient_demand
from shelfcash_core.contracts import ForecastPackage, ForecastPrediction
from shelfcash_core.exceptions import BOMError


class CoreBomAdapter:
    """Loads ORM state once, then invokes the pure deterministic BOM engine."""

    def __init__(self, session):
        self.session = session
        self.recipes = RecipeRepository(session)

    def expand(self, store_id, forecast_run, predictions, ingredient_scope=None):
        forecast = ForecastPackage(
            forecast_date=forecast_run.cutoff_date,
            forecast_horizon=forecast_run.horizon_days,
            model_version=forecast_run.model_version or "unknown",
            predictions=[
                ForecastPrediction(
                    store_id=store_id,
                    product_id=row.product_id,
                    product_name=row.product_name,
                    # Product sales are counts unless a future canonical product
                    # unit is supplied.  The core deliberately documents this.
                    unit=None,
                    target_date=row.target_date,
                    horizon=row.horizon,
                    p25=float(row.p25), p50=float(row.p50), p75=float(row.p75),
                    interval_lower=float(row.interval_lower),
                    interval_upper=float(row.interval_upper),
                    baseline_p50=float(row.baseline_p50),
                    calibration_source=row.calibration_source,
                    warnings=json.loads(row.warnings_json or "[]"),
                )
                for row in predictions
            ],
        )
        recipe_rows = []
        resolved_ingredient_ids = set()
        for row in predictions:
            active = self.recipes.get_active(store_id, row.product_id, row.target_date)
            if active is None:
                code = "RECIPE_NOT_EFFECTIVE" if self.recipes.get_versions(store_id, row.product_id) else "RECIPE_NOT_FOUND"
                raise PlanningError(code, "Recipe hợp lệ cho product forecast không tồn tại.", {
                    "product_id": row.product_id, "target_date": row.target_date.isoformat(),
                })
            lines = self.recipes.lines(active.recipe_version_id)
            if not lines:
                raise PlanningError("RECIPE_LINE_INVALID", "Recipe không có line.", {"recipe_version_id": active.recipe_version_id})
            for line in lines:
                ingredient = self.session.get(IngredientModel, line.ingredient_id)
                if ingredient is None or ingredient.store_id != store_id:
                    raise PlanningError("RECIPE_LINE_INVALID", "Ingredient trong recipe không hợp lệ.", {"recipe_line_id": line.recipe_line_id})
                recipe_rows.append({
                    "store_id": store_id,
                    "recipe_id": active.recipe_version_id,
                    "product_id": row.product_id,
                    "ingredient_id": line.ingredient_id,
                    "ingredient_name": ingredient.ingredient,
                    "ingredient_quantity": float(line.quantity),
                    "ingredient_unit": line.unit,
                    "yield_quantity": float(active.yield_quantity),
                    # Existing recipes have nullable yield_unit.  The prior
                    # backend interpreted product forecasts as recipe yields.
                    "yield_unit": active.yield_unit or "unit",
                    "process_loss_rate": float(active.process_loss_rate),
                    "waste_allowance_rate": 0.0,
                    "recipe_version": str(active.version),
                    "effective_from": active.effective_from,
                    "effective_to": active.effective_to,
                })
                resolved_ingredient_ids.add(line.ingredient_id)
        # Preserve the established scoped-demand contract.  A valid forecast
        # may deliberately ask for ingredients outside its active BOM.
        if ingredient_scope and not (set(ingredient_scope) & resolved_ingredient_ids):
            return []
        try:
            package = propagate_ingredient_demand(forecast, pd.DataFrame(recipe_rows))
        except BOMError as exc:
            raise PlanningError(exc.code, str(exc), exc.details) from exc
        fatal_issues = [issue for issue in package.issues if not issue.recoverable]
        if fatal_issues:
            issue = fatal_issues[0]
            raise PlanningError(issue.code, issue.message, issue.details)
        package_warnings = [*package.warnings, *(issue.code for issue in package.issues)]
        scope = set(ingredient_scope or [])
        return [
            {
                "ingredient_id": item.ingredient_id,
                "ingredient_name": item.ingredient_name or item.ingredient_id,
                "target_date": item.target_date,
                "horizon": next(row.horizon for row in predictions if row.target_date == item.target_date),
                "unit": item.unit,
                "p25": item.p25, "p50": item.p50, "p75": item.p75,
                "source_product_count": len({source.product_id for source in item.sources}),
                "contributions": [self._legacy_contribution(source) for source in item.sources],
                "warnings": sorted(set([*item.warnings, *package_warnings])),
            }
            for item in package.predictions
            if not scope or item.ingredient_id in scope
        ]

    @staticmethod
    def _legacy_contribution(source):
        """Keep the established API evidence keys while retaining core fields."""
        payload = source.model_dump(mode="json")
        payload.update({
            "product_p25": str(source.forecast_p25), "product_p50": str(source.forecast_p50),
            "product_p75": str(source.forecast_p75), "ingredient_p25": str(source.contribution_p25),
            "ingredient_p50": str(source.contribution_p50), "ingredient_p75": str(source.contribution_p75),
            "recipe_version_id": source.recipe_id,
        })
        return payload
