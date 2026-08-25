import re
from datetime import date, datetime
from typing import Any

import pandas as pd

DATE_FIELDS = {"date", "snapshot_date", "received_date", "expiry_date", "effective_date", "purchase_date"}
BOOLEAN_FIELDS = {"is_stockout", "is_weekend", "is_holiday", "is_store_closed", "is_promotion"}
UNIT_FIELDS = {"unit", "ingredient_unit", "yield_unit", "package_base_unit"}
NUMERIC_FIELDS = {
    "on_hand", "quantity_sold", "selling_price", "revenue", "quantity_used", "waste_quantity",
    "ingredient_quantity", "yield_quantity", "quantity_received", "unit_price", "total_cost",
    "minimum_order_quantity", "package_size", "lead_time_days", "temperature", "rainfall", "value",
    "list_price", "discount_rate", "savings_amount",
    "component_quantity",
}
UNIT_MAP = {"kg": "kilogram", "g": "gram", "l": "liter", "lit": "liter", "lít": "liter", "ml": "milliliter", "cai": "piece", "cái": "piece", "chiec": "piece", "chiếc": "piece", "ly": "cup", "hop": "box", "hộp": "box", "thung": "case", "thùng": "case", "chai": "bottle", "goi": "package", "gói": "package"}
TRUE_VALUES = {"true", "1", "yes", "y", "co", "có", "dung", "đúng"}
FALSE_VALUES = {"false", "0", "no", "n", "khong", "không", "sai"}


def normalize_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return pd.Timestamp(value).date().isoformat()
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?", text):
        parsed = pd.to_datetime(text, errors="coerce", format="ISO8601")
    else:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.date().isoformat()


def normalize_number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = re.sub(r"\s", "", str(value))
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts[-1]) == 3 else text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return None


def normalize_value(field: str, value: Any) -> Any:
    if field == "recipe_version":
        from app.core.exceptions import ValidationError
        from app.core.recipe_versions import normalize_recipe_version
        try:
            return normalize_recipe_version(value)
        except ValidationError:
            # Preserve invalid input so row validation can report its exact value
            # together with sheet and Excel row context.
            return value
    if field == "order_unit":
        if value is None or str(value).strip() == "":
            return None
        from app.core.packaging_units import normalize_packaging_unit
        return normalize_packaging_unit(value)
    if field == "item_type":
        from app.core.menu import normalize_item_type
        return normalize_item_type(value)
    if field == "status":
        from app.core.menu import normalize_menu_status
        return normalize_menu_status(value)
    if field == "selling_unit":
        from app.core.menu import normalize_product_unit
        return normalize_product_unit(value)
    if field in DATE_FIELDS:
        return normalize_date(value)
    if field in NUMERIC_FIELDS:
        return normalize_number(value)
    if field in BOOLEAN_FIELDS:
        text = str(value).strip().lower()
        return True if text in TRUE_VALUES else False if text in FALSE_VALUES else None
    if field in UNIT_FIELDS and value is not None:
        text = str(value).strip().lower()
        return UNIT_MAP.get(text, text)
    return value


def normalize_rows(rows, mapping, file_name, sheet_name, header_row):
    output = []
    for row_index, source_row in enumerate(rows):
        item = {target: normalize_value(target, source_row.get(source)) for source, target in mapping.items() if target}
        item.update(_source_file=file_name, _source_sheet=sheet_name, _source_excel_row=header_row + row_index + 2)
        output.append(item)
    return output
