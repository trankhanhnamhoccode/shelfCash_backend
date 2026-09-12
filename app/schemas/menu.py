from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


# ``MenuService.serialize`` returns ORM datetimes for a fresh response, while
# the idempotency replay path returns the timestamp strings it persisted.  Keep
# both current wire representations untouched at this public boundary.
WireDateTime = Annotated[
    str,
    BeforeValidator(lambda value: value.isoformat() if isinstance(value, datetime) else value),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


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


class MenuComponentResponse(BaseModel):
    """Current public component shape emitted by ``MenuService.serialize``."""

    model_config = ConfigDict(extra="forbid")

    bundle_line_id: str
    component_product_id: str
    component_sku: str | None
    component_product: str
    sku: str | None
    product: str
    quantity: int
    position: int
    price: int | None
    status: str


class MenuProductResponse(BaseModel):
    """Current public product shape shared by catalog and menu reads."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    store_id: str
    sku: str | None
    product: str
    item_type: str
    selling_unit: str | None
    price: int | None
    active: bool
    status: str
    list_price: int | None
    discount_rate: float
    savings_amount: int
    currency: str
    components: list[MenuComponentResponse]
    version: int
    created_at: WireDateTime
    updated_at: WireDateTime


class MenuSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    single_count: int
    combo_count: int
    active_count: int
    inactive_count: int


class MenuResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MenuProductResponse]
    summary: MenuSummaryResponse
    page: int
    page_size: int
    total: int
