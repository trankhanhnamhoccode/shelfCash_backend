import re
import unicodedata
from collections import defaultdict

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.schemas.llm import MappingSuggestion, SheetProfile


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value).lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[_/\-+]+", " ", value)).strip()


ALIASES = {
    "product_sku": ["ma mon"],
    "item_type": ["loai"],
    "combo_components": ["thanh phan combo"],
    "component_product_id": ["component product id", "id mon thanh phan"],
    "component_sku": ["component sku", "ma mon thanh phan", "ma thanh phan"],
    "component_product_name": ["component product name", "ten mon thanh phan", "ten thanh phan"],
    "component_variant": ["component variant", "bien the thanh phan", "dung tich thanh phan"],
    "component_quantity": ["component quantity", "so luong thanh phan", "sl thanh phan"],
    "selling_unit": ["dvt"],
    "list_price": ["tong gia le"],
    "discount_rate": ["muc giam"],
    "savings_amount": ["tiet kiem"],
    "status": ["trang thai"],
    "constraint_type": ["loai rang buoc", "loai dieu kien", "constraint type", "business rule"],
    "ingredient_name": ["nguyen lieu", "ten hang", "ten nl", "ap dung cho nl", "material", "ingredient", "ingredient name"],
    "effective_date": ["ngay hieu luc", "bat dau", "ap dung tu", "effective date"],
    "end_date": ["ngay ket thuc", "ket thuc", "ap dung den", "end date"],
    "note": ["ghi chu", "note", "description"],
    "value": ["gia tri", "value"],
    "unit": ["don vi", "dvt", "unit"],
    "date": ["ngay", "date"],
    "snapshot_date": ["ngay kiem ke", "snapshot date"],
    "on_hand": ["ton kho", "so luong ton", "on hand", "quantity on hand"],
    "product_name": ["san pham", "ten mon", "ten mon combo", "product", "product name"],
    "quantity_sold": ["so luong ban", "sl ban", "quantity sold"],
    "selling_price": ["gia ban", "selling price"],
    "revenue": ["doanh thu", "revenue"],
    "quantity_used": ["luong dung", "so luong su dung", "quantity used"],
    "waste_quantity": ["hao hut", "waste quantity"],
    "ingredient_quantity": ["dinh luong", "ingredient quantity"],
    "ingredient_unit": ["don vi nguyen lieu", "ingredient unit"],
    "yield_quantity": ["san luong", "yield quantity"],
    "recipe_version": ["phien ban cong thuc", "recipe version", "version cong thuc"],
    "purchase_date": ["ngay nhap", "purchase date"],
    "quantity_received": ["so luong nhap", "sl nhap", "quantity received"],
    "unit_price": ["don gia", "gia mua", "unit price"],
    "total_cost": ["thanh tien", "total cost"],
    "supplier_name": ["nha cung cap", "vendor", "supplier"],
    "external_record_id": ["external record id", "invoice id", "ma hoa don", "so hoa don"],
    "source": ["business source", "nguon nghiep vu", "source"],
    "minimum_order_quantity": ["so luong dat toi thieu", "moq", "minimum order quantity"],
    "order_unit": ["order uom", "order unit", "don vi dat hang"],
    "package_size": ["pack size", "package size", "quy cach dong goi"],
    "package_base_unit": ["base uom", "base unit", "don vi co so"],
    "lead_time_days": ["thoi gian giao", "lead time", "lead time days", "lead time (days)"],
    "shelf_life_days": ["shelf life days", "exact shelf life days", "so ngay han su dung"],
    "available_delivery_days": ["lich giao", "delivery schedule", "available delivery days"],
    "is_weekend": ["cuoi tuan", "is weekend"],
    "is_holiday": ["ngay le", "is holiday"],
    "temperature": ["nhiet do", "temperature"],
    "rainfall": ["luong mua", "rainfall"],
}

SHEET_KEYWORDS = {
    "menu": ["menu", "product catalog", "catalog", "menu items", "products menu"],
    "inventory": ["kiem ke", "ton kho", "inventory", "stock"],
    "sales_history": ["pos", "ban hang", "sales"],
    "usage_history": ["su dung", "tieu hao", "usage"],
    "recipes": ["dinh luong mon", "cong thuc", "recipe"],
    "purchase_history": ["pnk", "phieu nhap", "purchase", "receiving"],
    "supplier_constraints": ["vendor rules", "supplier constraint", "nha cung cap"],
    "calendar_features": ["calendar", "weather", "lich", "thoi tiet"],
    "business_constraints": [
        "rang buoc", "ngan sach", "constraint", "capacity", "safety stock", "dieu kien",
        "dieu kien van hanh", "business rule", "operating rule", "maximum stock", "service level",
    ],
}

MENU_UNMAPPED = {None, "", "ignore", "__unmapped__"}
MENU_HEADER_MAP = {
    "ma mon": "product_sku",
    "loai": "item_type",
    "ten mon combo": "product_name",
    "thanh phan combo": "combo_components",
    "component product id": "component_product_id",
    "component sku": "component_sku",
    "ma mon thanh phan": "component_sku",
    "ten mon thanh phan": "component_product_name",
    "bien the thanh phan": "component_variant",
    "dung tich thanh phan": "component_variant",
    "so luong thanh phan": "component_quantity",
    "dvt": "selling_unit",
    "tong gia le": "list_price",
    "muc giam": "discount_rate",
    "gia ban": "selling_price",
    "tiet kiem": "savings_amount",
    "trang thai": "status",
}


def menu_mapping_details(profile: SheetProfile, mapping: dict[str, str | None]) -> dict:
    columns = list(profile.columns)
    unresolved = [
        column for column in columns
        if mapping.get(column) is None
        or str(mapping.get(column)).strip().casefold() in MENU_UNMAPPED
    ]
    targets = [
        str(mapping[column]).strip() for column in columns
        if column not in unresolved
    ]
    duplicate_targets = sorted({
        target for target in targets if targets.count(target) > 1
    })
    missing_core = sorted(
        set(CANONICAL_SCHEMAS["menu"]["core_fields"]) - set(targets)
    )
    invalid_targets = sorted(
        set(targets) - set(CANONICAL_SCHEMAS["menu"]["fields"])
    )
    return {
        "profile_id": None,
        "sheet_name": profile.sheet_name,
        "unresolved_columns": unresolved,
        "missing_core_fields": missing_core,
        "duplicate_target_fields": duplicate_targets,
        "invalid_target_fields": invalid_targets,
    }


def _alias_map() -> dict[str, str]:
    result = {}
    for field, aliases in ALIASES.items():
        result[normalize_text(field)] = field
        result.update({normalize_text(alias): field for alias in aliases})
    return result


NORMALIZED_ALIASES = _alias_map()
STRUCTURAL_WARNING_PREFIXES = (
    "missing core fields",
    "multiple columns map to the same field",
    "fields outside schema",
    "mapping contains unknown source columns",
    "unknown source columns",
)


def validate_mapping(sheet_type: str, columns: list[str], mapping: dict[str, str | None]) -> tuple[list[str], list[str]]:
    if sheet_type not in CANONICAL_SCHEMAS:
        return [], [f"Unknown sheet type: {sheet_type}"]
    allowed = set(CANONICAL_SCHEMAS[sheet_type]["fields"])
    errors, warnings = [], []
    unknown_sources = sorted(set(mapping) - set(columns))
    if unknown_sources:
        errors.append(f"Mapping contains unknown source columns: {unknown_sources}")
    invalid = sorted({field for field in mapping.values() if field is not None and field not in allowed})
    if invalid:
        errors.append(f"Fields outside schema '{sheet_type}': {invalid}")
    duplicates = defaultdict(list)
    for source, field in mapping.items():
        if field:
            duplicates[field].append(source)
    duplicate_fields = {field: sources for field, sources in duplicates.items() if len(sources) > 1}
    if duplicate_fields:
        warnings.append(f"Multiple columns map to the same field: {duplicate_fields}")
    mapped = {field for field in mapping.values() if field}
    missing = sorted(set(CANONICAL_SCHEMAS[sheet_type]["core_fields"]) - mapped)
    if missing:
        warnings.append(f"Missing core fields: {missing}")
    return warnings, errors


def _is_structural_warning(message: str) -> bool:
    normalized = message.strip().lower()
    return any(normalized.startswith(prefix) for prefix in STRUCTURAL_WARNING_PREFIXES)


def finalize_mapping(
    profile: SheetProfile,
    suggestion: MappingSuggestion,
    confidence_threshold: float,
    *,
    manual: bool = False,
) -> MappingSuggestion:
    complete_mapping = {column: suggestion.column_mapping.get(column) for column in profile.columns}
    fresh_warnings, fresh_errors = validate_mapping(
        suggestion.sheet_type, profile.columns, complete_mapping
    )
    semantic_warnings = [
        warning for warning in suggestion.warnings if not _is_structural_warning(warning)
    ]
    warnings = list(dict.fromkeys([*semantic_warnings, *fresh_warnings]))
    errors = list(dict.fromkeys(fresh_errors))
    missing_core = any(warning.lower().startswith("missing core fields") for warning in warnings)
    confidence_review = False if manual else suggestion.confidence < confidence_threshold
    return MappingSuggestion(
        sheet_type=suggestion.sheet_type,
        confidence=1.0 if manual else suggestion.confidence,
        column_mapping=complete_mapping,
        warnings=warnings,
        errors=errors,
        source=suggestion.source,
        requires_review=bool(errors or missing_core or confidence_review),
        raw_response=suggestion.raw_response,
    )


def map_sheet_rules(
    profile: SheetProfile, confidence_threshold: float = 0.82
) -> MappingSuggestion:
    name = normalize_text(profile.sheet_name)
    scores = {kind: sum(1 for keyword in keywords if normalize_text(keyword) in name) for kind, keywords in SHEET_KEYWORDS.items()}
    sheet_type = max(scores, key=scores.get) if max(scores.values(), default=0) else "unknown"
    allowed = set(CANONICAL_SCHEMAS[sheet_type]["fields"])
    mapping = {}
    matches = 0
    for column in profile.columns:
        target = (
            MENU_HEADER_MAP.get(normalize_text(column))
            if sheet_type == "menu"
            else NORMALIZED_ALIASES.get(normalize_text(column))
        )
        target = target if target in allowed else None
        mapping[column] = target
        matches += target is not None
    keyword_score = min(1.0, scores.get(sheet_type, 0) / 1.0)
    column_score = matches / max(1, len(profile.columns))
    confidence = round(0.55 * keyword_score + 0.45 * column_score, 3) if sheet_type != "unknown" else round(0.35 * column_score, 3)
    warnings, errors = validate_mapping(sheet_type, profile.columns, mapping)
    suggestion = MappingSuggestion(
        sheet_type=sheet_type,
        confidence=confidence,
        column_mapping=mapping,
        warnings=warnings,
        errors=errors,
        source="rule",
        requires_review=False,
    )
    return finalize_mapping(profile, suggestion, confidence_threshold)
