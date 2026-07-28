import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _stable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in sorted(value.items()) if not str(key).startswith("_volatile")}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_row_hash(*, store_id: str, import_id: str, profile_id: str, sheet_id: str,
                    source_row: int, sheet_type: str, row: dict[str, Any]) -> str:
    return canonical_hash({
        "store_id": store_id, "import_id": import_id, "profile_id": profile_id,
        "sheet_id": sheet_id, "source_row": source_row, "sheet_type": sheet_type,
        "row": row,
    })


def purchase_business_key(**fields: Any) -> str:
    return canonical_hash(fields)
