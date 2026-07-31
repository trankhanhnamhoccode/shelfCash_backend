from typing import Any

from app.core.canonical_schemas import CANONICAL_SCHEMAS


def validate_records(sheet_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    core = CANONICAL_SCHEMAS[sheet_type]["core_fields"]
    errors = []
    for index, record in enumerate(records):
        missing = [field for field in core if record.get(field) is None or record.get(field) == ""]
        if sheet_type == "menu":
            from app.core.menu import components_empty
            item_type = record.get("item_type")
            components = record.get("combo_components")
            has_explicit_component = any(record.get(field) not in (None, "") for field in (
                "component_product_id", "component_sku", "component_product_name",
            ))
            if item_type == "single" and not components_empty(components):
                missing.append("combo_components_must_be_empty")
            if item_type == "combo" and components_empty(components) and not has_explicit_component:
                missing.append("combo_components")
        if missing:
            errors.append({"row": index + 1, "missing_core_fields": missing})
    return {"row_count": len(records), "valid_rows": len(records) - len(errors), "invalid_rows": len(errors), "errors": errors[:100]}
