from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from app.schemas.operational import CalendarFeatureResponse, Page


class _CompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MovementDeltaResponse(_CompletionResponse):
    movement_id: str | None
    lot_id: str
    before_quantity: str
    quantity_delta: str
    after_quantity: str
    version: int


class InventoryCountResultResponse(_CompletionResponse):
    inventory_count_id: str
    adjustments: list[MovementDeltaResponse]
    # The current runtime deliberately returns an empty list.  Keep the field
    # without implying an inventory-view schema that is not emitted here.
    inventory: list[dict[str, JsonValue]]


class InventoryAdjustmentResultResponse(_CompletionResponse):
    inventory_adjustment_id: str
    occurred_at: datetime
    movements: list[MovementDeltaResponse]


class SalesBatchRecordResponse(_CompletionResponse):
    external_record_id: str
    sales_record_id: str
    status: str


class UsageRebuildResponse(_CompletionResponse):
    status: str
    warning_count: int


class SalesBatchResultResponse(_CompletionResponse):
    batch_id: str
    created_count: int
    unchanged_count: int
    records: list[SalesBatchRecordResponse]
    usage_rebuild: UsageRebuildResponse
    # Reconciliation emits structured warning records (and historically may
    # include scalar messages), so retain the current bounded JSON boundary.
    warnings: list[JsonValue]


class PurchaseBatchRecordResponse(_CompletionResponse):
    external_record_id: str | None
    purchase_record_id: str
    status: str


class PurchaseBatchResultResponse(_CompletionResponse):
    batch_id: str
    created_count: int
    unchanged_count: int
    records: list[PurchaseBatchRecordResponse]
    inventory_applied: bool


class SupplierTermWriteResponse(_CompletionResponse):
    constraint_id: str
    ingredient_id: str
    supplier_id: str
    supplier: str
    unit_cost: int
    moq: str
    pack_size: str
    order_unit: str | None
    lead_time_days: int
    shelf_life_days: int | None
    unit: str
    version: int
    active: bool


class StoreSettingsResponse(_CompletionResponse):
    monthly_budget: int
    reserved_budget: int
    spent_budget: int
    remaining_budget: int
    forecast_horizon: int
    default_strategy: str
    safety_policy: None
    version: int
    updated_at: datetime | None


class CalendarWriteResultResponse(_CompletionResponse):
    created_count: int
    updated_count: int
    unchanged_count: int
    items: list[CalendarFeatureResponse]


class PurchaseOrderLineResponse(_CompletionResponse):
    po_line_id: str
    ingredient_id: str
    order_quantity: str
    received_quantity: str
    unit: str
    unit_cost: int
    line_total: int
    shelf_life_days: int | None
    projected_expiry_date: date | None


class PurchaseOrderResponse(_CompletionResponse):
    po_id: str
    supplier_id: str
    supplier: str | None
    plan_run_id: str
    order_date: date
    delivery_date: date
    strategy: str
    status: str
    lines: list[PurchaseOrderLineResponse]
    total: int
    budget_after: int
    version: int
    confirmed_at: datetime | None
    received_at: datetime | None


class PurchaseOrderCreateResponse(_CompletionResponse):
    orders: list[PurchaseOrderResponse]


PurchaseOrderPageResponse = Page[PurchaseOrderResponse]
