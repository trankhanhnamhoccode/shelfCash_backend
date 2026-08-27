from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
StoreId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)]


class IngredientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient: Name
    sku: Sku | None = None
    base_unit: Name
    active: bool = True
    expiry_tracking_mode: Literal["required", "not_required", "unknown"] | None = None


class IngredientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    ingredient: Name | None = None
    sku: Sku | None = None
    base_unit: Name | None = None
    active: bool | None = None
    expiry_tracking_mode: Literal["required", "not_required", "unknown"] | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not self.model_fields_set.intersection({"ingredient", "sku", "base_unit", "active", "expiry_tracking_mode"}):
            raise ValueError("At least one update field is required")
        return self


class IngredientResponse(BaseModel):
    ingredient_id: str
    store_id: str
    ingredient: str
    sku: str | None
    base_unit: str
    aliases: list[str]
    active: bool
    expiry_tracking_mode: Literal["required", "not_required", "unknown"]
    expiry_tracking_source: Literal["declared", "inferred"]
    version: int
    created_at: datetime
    updated_at: datetime


class AliasCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: str
    alias: Name


class AliasUpsertItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: Name
    canonical_name: Name
    ingredient_id: str | None = None


class AliasBulkUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aliases: list[AliasUpsertItem] = Field(min_length=1)


class AliasResponse(BaseModel):
    alias_id: str
    store_id: str
    ingredient_id: str
    alias: str
    created_at: datetime


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: Name
    sku: Sku | None = None
    price: int | None = Field(default=None, ge=0)
    active: bool = True


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    product: Name | None = None
    sku: Sku | None = None
    price: int | None = Field(default=None, ge=0)
    active: bool | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not self.model_fields_set.intersection({"product", "sku", "price", "active"}):
            raise ValueError("At least one update field is required")
        return self


class ProductResponse(BaseModel):
    product_id: str
    store_id: str
    product: str
    sku: str | None
    price: int | None
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime
