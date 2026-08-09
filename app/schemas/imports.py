from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic import model_validator

from app.schemas.llm import MappingSuggestion, SheetProfile


class ImportSheet(BaseModel):
    sheet_id: str
    profile_id: UUID | None = None
    profile: SheetProfile
    mapping: MappingSuggestion


class ImportProfileResponse(BaseModel):
    profile_id: UUID
    sheet_id: str
    file_name: str
    sheet_name: str
    header_row_zero_based: int
    row_count: int
    column_count: int
    columns: list[str]
    dtypes: dict[str, str]
    sample_rows: list[dict[str, Any]]


class SuggestedMappingResponse(BaseModel):
    profile_id: UUID
    sheet_id: str
    sheet_name: str
    sheet_type: str
    confidence: float
    column_mapping: dict[str, str | None]
    warnings: list[str]
    errors: list[str]
    source: str
    requires_review: bool


class ImportResponse(BaseModel):
    import_id: UUID
    status: str
    store_id: str
    forecast_date: date | None
    forecast_horizon: int
    sheets: list[ImportSheet]
    profiles: list[ImportProfileResponse] = Field(default_factory=list)
    suggested_mappings: list[SuggestedMappingResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    requires_review: bool
    created_at: datetime


class ConfirmedMapping(BaseModel):
    sheet_id: str | None = None
    profile_id: UUID | None = None
    sheet_type: str = "unknown"
    column_mapping: dict[str, str | None] = Field(default_factory=dict)
    skip: bool = False

    @model_validator(mode="after")
    def identifier_is_required(self):
        if self.sheet_id is None and self.profile_id is None:
            raise ValueError("sheet_id or profile_id is required")
        return self


class ConfirmRequest(BaseModel):
    mappings: list[ConfirmedMapping]


class StatusResponse(BaseModel):
    import_id: UUID
    status: str
    mappings: list[ImportSheet]
    sheets: list[ImportSheet] = Field(default_factory=list)
    profiles: list[ImportProfileResponse] = Field(default_factory=list)
    suggested_mappings: list[SuggestedMappingResponse] = Field(default_factory=list)
    requires_review: bool


class ProcessResponse(BaseModel):
    import_id: UUID
    status: str
    validation_summary: dict[str, Any]
    processing_policy: str = "atomic"
    issues: list[dict[str, Any]] = Field(default_factory=list)
