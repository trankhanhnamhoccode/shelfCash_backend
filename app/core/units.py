import unicodedata
from decimal import Decimal

from app.core.exceptions import ValidationError

_ALIASES = {
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gram": "g", "grams": "g",
    "l": "lít", "liter": "lít", "litre": "lít", "liters": "lít",
    "litres": "lít", "lít": "lít",
    "ml": "ml", "milliliter": "ml", "millilitre": "ml",
    "piece": "cái", "pieces": "cái", "pcs": "cái", "cái": "cái",
}
_DIMENSIONS = {"kg": "mass", "g": "mass", "lít": "volume", "ml": "volume", "cái": "count"}
_TO_BASE = {"kg": Decimal("1000"), "g": Decimal("1"), "lít": Decimal("1000"), "ml": Decimal("1"), "cái": Decimal("1")}


def normalize_unit(value: str) -> str:
    key = unicodedata.normalize("NFC", value).strip().casefold()
    unit = _ALIASES.get(key)
    if unit is None:
        raise ValidationError("Đơn vị không được hỗ trợ.", {"unit": value})
    return unit


def unit_dimension(value: str) -> str:
    return _DIMENSIONS[normalize_unit(value)]


def validate_compatible(from_unit: str, to_unit: str) -> None:
    if unit_dimension(from_unit) != unit_dimension(to_unit):
        raise ValidationError("Không thể chuyển đổi giữa các nhóm đơn vị.", {"from_unit": from_unit, "to_unit": to_unit})


def convert_quantity(quantity: Decimal | str | int, from_unit: str, to_unit: str) -> Decimal:
    source, target = normalize_unit(from_unit), normalize_unit(to_unit)
    validate_compatible(source, target)
    return Decimal(str(quantity)) * _TO_BASE[source] / _TO_BASE[target]
