from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.canonical_schemas import CANONICAL_SCHEMAS

SheetType = Literal[
    "inventory", "sales_history", "usage_history", "recipes", "purchase_history",
    "supplier_constraints", "calendar_features", "business_constraints", "menu", "unknown",
]


class SheetProfile(BaseModel):
    file_name: str
    sheet_name: str
    header_row_zero_based: int = Field(ge=0)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[str]
    dtypes: dict[str, str]
    sample_rows: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class MappingSuggestion(BaseModel):
    sheet_type: SheetType
    confidence: float = Field(ge=0, le=1)
    column_mapping: dict[str, str | None]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    source: Literal["rule", "llm", "rule_fallback"] = "rule"
    requires_review: bool = False
    raw_response: dict[str, Any] | str | None = None

    @model_validator(mode="after")
    def fields_belong_to_schema(self):
        allowed = set(CANONICAL_SCHEMAS[self.sheet_type]["fields"])
        invalid = sorted({v for v in self.column_mapping.values() if v is not None and v not in allowed})
        if invalid:
            raise ValueError(f"Fields outside schema '{self.sheet_type}': {invalid}")
        return self


class MapSheetRequest(BaseModel):
    profile: SheetProfile
