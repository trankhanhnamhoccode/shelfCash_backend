from typing import Any

from pydantic import BaseModel, Field


class CanonicalResult(BaseModel):
    store_id: str
    forecast_date: str | None
    forecast_horizon: int
    inventory: list[dict[str, Any]] = Field(default_factory=list)
    sales_history: list[dict[str, Any]] = Field(default_factory=list)
    usage_history: list[dict[str, Any]] = Field(default_factory=list)
    recipes: list[dict[str, Any]] = Field(default_factory=list)
    purchase_history: list[dict[str, Any]] = Field(default_factory=list)
    supplier_constraints: list[dict[str, Any]] = Field(default_factory=list)
    calendar_features: list[dict[str, Any]] = Field(default_factory=list)
    business_constraints: list[dict[str, Any]] = Field(default_factory=list)
    menu: list[dict[str, Any]] = Field(default_factory=list)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    ingestion_metadata: dict[str, Any] = Field(default_factory=dict)
