from typing import Any

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.exceptions import ValidationError
from app.core.recipe_versions import normalize_recipe_version


def validate_records(sheet_type: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    core = CANONICAL_SCHEMAS[sheet_type]["core_fields"]
    errors = []
    for index, record in enumerate(records):
        missing = [field for field in core if record.get(field) is None or record.get(field) == ""]
        if sheet_type == "recipes":
            try:
                record["recipe_version"] = normalize_recipe_version(record.get("recipe_version"))
            except ValidationError:
                value = record.get("recipe_version")
                errors.append({
                    "sheet": record.get("_source_sheet"),
                    "row": index + 1,
                    "row_number": record.get("_source_excel_row", index + 1),
                    "field": "recipe_version",
                    "code": "INVALID_RECIPE_VERSION",
                    "message": "recipe_version must be a positive integer such as 1, 2, 3",
                    "raw_value": value,
                    "value": value,
                    "remediation": "Use a positive integer, optionally prefixed with 'v', or leave blank for automatic versioning.",
                })
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
        for field in missing:
            errors.append({
                "sheet": record.get("_source_sheet"),
                "row": index + 1,
                "row_number": record.get("_source_excel_row", index + 1),
                "field": field,
                "code": "CORE_FIELD_MISSING",
                "message": f"Required canonical field '{field}' is missing or invalid.",
                "raw_value": record.get(field),
                "remediation": f"Map or provide a valid value for '{field}'.",
                "missing_core_fields": missing,
            })
    invalid_rows = len({item["row"] for item in errors})
    return {"row_count": len(records), "valid_rows": len(records) - invalid_rows, "invalid_rows": invalid_rows, "errors": errors[:100]}
