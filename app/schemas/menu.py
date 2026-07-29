from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ComponentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component_product_id: str
    quantity: int = Field(gt=0)


class MenuProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: Name
    sku: Sku | None = None
    item_type: str = "single"
    selling_unit: str | None = None
    price: int | None = Field(default=None, ge=0)
    status: str | None = None
    active: bool | None = None
    components: list[ComponentWrite] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def compatible_status(self):
        if self.status is not None and self.active is not None:
            expected = self.status == "active"
            if expected != self.active:
                raise ValueError("status and active disagree")
        return self


class MenuProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    product: Name | None = None
    sku: Sku | None = None
    price: int | None = Field(default=None, ge=0)
    status: str | None = None
    active: bool | None = None
    selling_unit: str | None = None
    item_type: str | None = None

    @model_validator(mode="after")
    def has_change(self):
        if not self.model_fields_set.intersection(
            {"product", "sku", "price", "status", "active", "selling_unit", "item_type"}
        ):
            raise ValueError("At least one update field is required")
        return self


class ComponentsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    components: list[ComponentWrite] = Field(min_length=1, max_length=20)
