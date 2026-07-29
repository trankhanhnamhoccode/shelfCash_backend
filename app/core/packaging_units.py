import unicodedata

from app.core.exceptions import ValidationError


_ALIASES = {
    "thùng": "case",
    "case": "case",
    "carton": "case",
    "bao": "bag",
    "bag": "bag",
    "sack": "bag",
    "gói": "pack",
    "pack": "pack",
    "packet": "pack",
    "hộp": "box",
    "box": "box",
    "két": "crate",
    "crate": "crate",
    "bó": "bundle",
    "bundle": "bundle",
}


def packaging_unit_key(value: str | None) -> str:
    if value is None:
        raise ValidationError(
            "Thiếu đơn vị đóng gói.",
            {"field": "order_unit", "value": None},
        )
    key = unicodedata.normalize("NFC", str(value)).strip().casefold()
    if not key:
        raise ValidationError(
            "Thiếu đơn vị đóng gói.",
            {"field": "order_unit", "value": value},
        )
    return key


def normalize_packaging_unit(value: str | None) -> str:
    """Normalize packaging semantics without assigning a physical dimension."""
    key = packaging_unit_key(value)
    return _ALIASES.get(key, key)


def is_known_packaging_unit(value: str | None) -> bool:
    try:
        return packaging_unit_key(value) in _ALIASES
    except ValidationError:
        return False
