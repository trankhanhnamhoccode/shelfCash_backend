from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.normalizer import normalize_rows
from app.core.rule_mapper import map_sheet_rules, validate_mapping
from app.core.validator import validate_records
from app.schemas.llm import MappingSuggestion


class IngestionPipeline:
    def __init__(self, llm_provider, confidence_threshold: float):
        self.llm_provider = llm_provider
        self.confidence_threshold = confidence_threshold

    async def suggest(self, profile):
        rule = map_sheet_rules(profile)
        if rule.confidence >= self.confidence_threshold:
            return rule
        if self.llm_provider.available:
            return await self.llm_provider.map_sheet(profile, CANONICAL_SCHEMAS, rule)
        rule.source = "rule_fallback"
        rule.requires_review = True
        return rule

    def confirm(self, profile, sheet_type: str, column_mapping: dict[str, str | None]) -> MappingSuggestion:
        complete_mapping = {column: column_mapping.get(column) for column in profile.columns}
        warnings, errors = validate_mapping(sheet_type, profile.columns, complete_mapping)
        if errors:
            raise ValueError("; ".join(errors))
        return MappingSuggestion(
            sheet_type=sheet_type, confidence=1.0, column_mapping=complete_mapping,
            warnings=warnings, errors=[], source="rule", requires_review=bool(warnings),
        )

    def process_sheet(self, sheet):
        mapping = sheet["mapping"]
        sheet_type = mapping["sheet_type"]
        if sheet_type == "unknown":
            return sheet_type, [], {"row_count": 0, "valid_rows": 0, "invalid_rows": 0, "errors": []}
        profile = sheet["profile"]
        records = normalize_rows(
            sheet["rows"], mapping["column_mapping"], profile["file_name"], profile["sheet_name"],
            profile["header_row_zero_based"],
        )
        return sheet_type, records, validate_records(sheet_type, records)
