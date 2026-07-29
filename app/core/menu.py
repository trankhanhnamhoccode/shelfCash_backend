import re
import unicodedata
from dataclasses import dataclass

from app.core.names import display_name, normalize_name
from app.core.exceptions import ValidationError


PRODUCT_UNITS = {"ly", "phần", "chai", "cái", "combo"}
EMPTY_COMPONENT_MARKERS = {"", "-", "—", "–", "none", "null", "n/a"}


def _fold(value) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")


def normalize_item_type(value) -> str:
    folded = re.sub(r"\s+", " ", _fold(value))
    if folded in {"mon le", "single", "retail"}:
        return "single"
    if folded in {"combo", "bundle"}:
        return "combo"
    raise ValidationError("Loại Menu không hợp lệ.", {"code": "INVALID_MENU_ITEM_TYPE", "value": value})


def normalize_menu_status(value) -> str:
    folded = re.sub(r"\s+", " ", _fold(value))
    if folded in {"dang ban", "active", "enabled"}:
        return "active"
    if folded in {"ngung ban", "inactive", "disabled"}:
        return "inactive"
    raise ValidationError("Trạng thái Menu không hợp lệ.", {"code": "INVALID_MENU_STATUS", "value": value})


def normalize_product_unit(value) -> str:
    folded = re.sub(r"\s+", " ", _fold(value))
    aliases = {"phan": "phần", "cai": "cái"}
    unit = aliases.get(folded, folded)
    if unit not in PRODUCT_UNITS:
        raise ValidationError("Đơn vị bán không hợp lệ.", {"code": "INVALID_PRODUCT_UNIT", "value": value})
    return unit


def components_empty(value) -> bool:
    return _fold(value) in {_fold(marker) for marker in EMPTY_COMPONENT_MARKERS}


@dataclass(frozen=True)
class ParsedComponent:
    quantity: int
    product_name: str
    normalized_name: str


def parse_combo_components(value, *, maximum: int = 20) -> list[ParsedComponent]:
    if components_empty(value):
        raise ValidationError("Combo phải có components.", {"code": "COMBO_COMPONENTS_REQUIRED"})
    segments = [segment.strip() for segment in str(value).split("+")]
    if not segments or len(segments) > maximum:
        raise ValidationError("Số component combo không hợp lệ.", {
            "code": "COMBO_COMPONENT_PARSE_ERROR", "maximum": maximum,
        })
    parsed: list[ParsedComponent] = []
    seen: set[str] = set()
    pattern = re.compile(r"^\s*(\d+)\s*[×xX*]\s*(.+?)\s*$")
    for segment in segments:
        match = pattern.fullmatch(segment)
        if not match or int(match.group(1)) <= 0:
            raise ValidationError("Không đọc được component combo.", {
                "code": "COMBO_COMPONENT_PARSE_ERROR", "segment": segment,
            })
        name = display_name(match.group(2))
        normalized = normalize_name(name)
        if normalized in seen:
            raise ValidationError("Component combo bị trùng.", {
                "code": "COMBO_COMPONENT_DUPLICATE", "component": name,
            })
        seen.add(normalized)
        parsed.append(ParsedComponent(int(match.group(1)), name, normalized))
    return parsed
