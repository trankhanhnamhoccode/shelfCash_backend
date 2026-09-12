from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from app.schemas.recipes import RecipeLineResponse


ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """Current operational list envelope; this is a response-only DTO utility."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    page: int
    page_size: int
    total: int


class ImportSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str
    store_id: str
    status: str
    forecast_date: date | None
    forecast_horizon: int
    requires_review: bool
    file_count: int
    profile_count: int
    warning_count: int
    error_count: int
    created_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None


class RecipeVersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_version_id: str
    version: int
    effective_from: date
    effective_to: date | None
    content_hash: str
    lines: list[RecipeLineResponse]
    created_at: datetime


class SalesHistoryRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sales_record_id: str
    store_id: str
    date: date
    product_id: str
    quantity: float
    unit_price: int | None
    promotion: bool
    is_stockout: bool | None
    source: str
    import_id: str | None
    created_at: datetime
    updated_at: datetime | None
    external_record_id: str | None


class UsageHistoryRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usage_record_id: str
    store_id: str
    date: date
    ingredient_id: str
    quantity: float
    unit: str
    source: str
    usage_source: str | None
    waste_quantity: float
    import_id: str | None
    created_at: datetime
    updated_at: datetime | None


class PurchaseHistoryRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    store_id: str
    ingredient_id: str
    supplier_id: str | None
    receipt_date: date
    quantity: float
    unit: str
    unit_cost: int | None
    total_cost: float | None
    purchase_order_id: str | None
    expiry_date: date | None
    batch_code: str | None
    source: str
    import_id: str | None
    created_at: datetime
    external_record_id: str | None
    inventory_effect: str
    po_id: str | None
    po_line_id: str | None


class InventoryLotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: str
    ingredient_id: str
    ingredient: str | None
    sku: str | None
    on_hand: float
    usable_quantity: float
    expiring_quantity: float
    expired_quantity: float
    unit: str
    unit_cost: int | None
    batch_code: str | None
    received_date: date | None
    received_date_status: str
    expiry_date: date | None
    supplier_id: str | None
    supplier: str | None
    status: str
    snapshot_date: date | None
    version: int


class InventoryMovementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_id: str
    store_id: str
    lot_id: str
    movement_type: str
    quantity_delta: float
    unit: str
    occurred_at: datetime
    source: str
    source_id: str | None
    source_import_id: str | None
    source_profile_id: str | None
    note: str | None
    created_at: datetime


class CalendarFeatureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    weekday: str
    weekend: bool
    holiday: bool
    promotion: bool
    promotion_note: str | None
