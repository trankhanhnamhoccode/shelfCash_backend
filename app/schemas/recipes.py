from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RecipeLineWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: str
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    unit: str


class RecipeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: date
    version: int = Field(ge=0)
    yield_quantity: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)
    process_loss_rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1, allow_inf_nan=False)
    lines: list[RecipeLineWrite] = Field(min_length=1)


class RecipeLineResponse(BaseModel):
    recipe_line_id: str
    ingredient_id: str
    ingredient: str
    quantity: str
    unit: str


class RecipeVersionResponse(BaseModel):
    recipe_version_id: str
    version: int
    effective_from: date
    effective_to: date | None
    yield_quantity: str
    process_loss_rate: str
    content_hash: str
    lines: list[RecipeLineResponse]
    created_at: datetime


class ActiveRecipeResponse(BaseModel):
    product_id: str
    store_id: str
    recipe: RecipeVersionResponse | None
