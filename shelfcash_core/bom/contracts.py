from __future__ import annotations

from datetime import date
from math import isclose
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictBOMContract(BaseModel):
    """Strict, finite-number contract shared by Recipe/BOM payloads."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RecipeRecord(StrictBOMContract):
    recipe_line_id: str | None = None
    recipe_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    ingredient_id: str = Field(min_length=1)
    ingredient_name: str | None = None
    ingredient_quantity: float = Field(ge=0)
    ingredient_unit: str = Field(min_length=1)
    yield_quantity: float = Field(gt=0)
    yield_unit: str = Field(min_length=1)
    process_loss_rate: float = Field(default=0, ge=0)
    waste_allowance_rate: float = Field(default=0, ge=0)
    recipe_version: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None

    @field_validator(
        "recipe_id",
        "product_id",
        "ingredient_id",
        "ingredient_unit",
        "yield_unit",
        "recipe_version",
        mode="before",
    )
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_effective_range(self) -> RecipeRecord:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to không được trước effective_from.")
        return self


class UnitConversionRule(StrictBOMContract):
    ingredient_id: str = Field(min_length=1)
    from_unit: str = Field(min_length=1)
    to_unit: str = Field(min_length=1)
    factor: float = Field(gt=0)


class BOMIssue(StrictBOMContract):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    recoverable: bool
    suggested_action: str | None = None


class IngredientDemandSource(StrictBOMContract):
    product_id: str
    product_name: str
    product_unit: str | None = None
    recipe_id: str
    recipe_version: str
    recipe_line_id: str | None = None
    forecast_p25: float = Field(ge=0)
    forecast_p50: float = Field(ge=0)
    forecast_p75: float = Field(ge=0)
    recipe_quantity: float = Field(ge=0)
    recipe_unit: str
    yield_quantity: float = Field(gt=0)
    yield_unit: str
    process_loss_rate: float = Field(ge=0)
    waste_allowance_rate: float = Field(ge=0)
    base_contribution_p25: float = Field(ge=0)
    base_contribution_p50: float = Field(ge=0)
    base_contribution_p75: float = Field(ge=0)
    contribution_p25: float = Field(ge=0)
    contribution_p50: float = Field(ge=0)
    contribution_p75: float = Field(ge=0)
    contribution_unit: str

    @model_validator(mode="after")
    def validate_quantile_ordering(self) -> IngredientDemandSource:
        groups = (
            (self.forecast_p25, self.forecast_p50, self.forecast_p75),
            (
                self.base_contribution_p25,
                self.base_contribution_p50,
                self.base_contribution_p75,
            ),
            (self.contribution_p25, self.contribution_p50, self.contribution_p75),
        )
        if any(not low <= median <= high for low, median, high in groups):
            raise ValueError("BOM source quantiles phải thỏa P25 <= P50 <= P75.")
        return self


class IngredientDemandPrediction(StrictBOMContract):
    store_id: str
    ingredient_id: str
    ingredient_name: str | None = None
    target_date: date
    p25: float = Field(ge=0)
    p50: float = Field(ge=0)
    p75: float = Field(ge=0)
    unit: str = Field(min_length=1)
    sources: list[IngredientDemandSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ordering(self) -> IngredientDemandPrediction:
        if not self.p25 <= self.p50 <= self.p75:
            raise ValueError("Ingredient quantiles phải thỏa P25 <= P50 <= P75.")
        totals = (
            sum(source.contribution_p25 for source in self.sources),
            sum(source.contribution_p50 for source in self.sources),
            sum(source.contribution_p75 for source in self.sources),
        )
        if not all(
            isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
            for actual, expected in zip((self.p25, self.p50, self.p75), totals, strict=True)
        ):
            raise ValueError("Ingredient demand quantiles must equal source contributions.")
        identities = [
            (
                source.product_id,
                source.recipe_id,
                source.recipe_version,
                self.ingredient_id,
                source.recipe_line_id
                or (
                    source.recipe_quantity,
                    source.recipe_unit,
                    source.yield_quantity,
                    source.yield_unit,
                    source.process_loss_rate,
                    source.waste_allowance_rate,
                ),
            )
            for source in self.sources
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Duplicate semantic BOM contribution detected.")
        return self


class IngredientDemandPackage(StrictBOMContract):
    forecast_date: date
    forecast_horizon: int = Field(ge=1)
    forecast_model_version: str
    predictions: list[IngredientDemandPrediction]
    issues: list[BOMIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_complete: bool = True
