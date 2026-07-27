from typing import Any

from app.core.canonical_schemas import CANONICAL_SCHEMAS


def validate_records(sheet_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    core = CANONICAL_SCHEMAS[sheet_type]["core_fields"]
    errors = []
    for index, record in enumerate(records):
        missing = [field for field in core if record.get(field) is None or record.get(field) == ""]
        if missing:
            errors.append({"row": index + 1, "missing_core_fields": missing})
    return {"row_count": len(records), "valid_rows": len(records) - len(errors), "invalid_rows": len(errors), "errors": errors[:100]}
