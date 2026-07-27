from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.llm import MappingSuggestion, SheetProfile


class ImportSheet(BaseModel):
    sheet_id: str
    profile: SheetProfile
    mapping: MappingSuggestion


class ImportResponse(BaseModel):
    import_id: UUID
    status: str
    store_id: str
    forecast_date: date | None
    forecast_horizon: int
    sheets: list[ImportSheet]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    requires_review: bool
    created_at: datetime


class ConfirmedMapping(BaseModel):
    sheet_id: str
    sheet_type: str
    column_mapping: dict[str, str | None]


class ConfirmRequest(BaseModel):
    mappings: list[ConfirmedMapping]


class StatusResponse(BaseModel):
    import_id: UUID
    status: str
    mappings: list[ImportSheet]
    requires_review: bool


class ProcessResponse(BaseModel):
    import_id: UUID
    status: str
    validation_summary: dict[str, Any]
