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


def normalize_unit(value: str | None) -> str:
    safe_value = "None" if value is None else str(value)
    key = unicodedata.normalize("NFC", safe_value).strip().casefold()
    if key == "none":
        return "None"
    unit = _ALIASES.get(key)
    if unit is None:
        raise ValidationError("Đơn vị không được hỗ trợ.", {"unit": value})
    return unit


def unit_dimension(value: str | None) -> str:
    unit = normalize_unit(value)
    return "missing" if unit == "None" else _DIMENSIONS[unit]


def validate_compatible(from_unit: str | None, to_unit: str | None) -> None:
    source_dimension = unit_dimension(from_unit)
    target_dimension = unit_dimension(to_unit)
    if "missing" in {source_dimension, target_dimension}:
        return
    if source_dimension != target_dimension:
        raise ValidationError("Không thể chuyển đổi giữa các nhóm đơn vị.", {"from_unit": from_unit, "to_unit": to_unit})


def convert_quantity(quantity: Decimal | str | int, from_unit: str | None, to_unit: str | None) -> Decimal:
    value = Decimal(str(quantity))
    source, target = normalize_unit(from_unit), normalize_unit(to_unit)
    if "None" in {source, target}:
        return value
    validate_compatible(source, target)
    return value * _TO_BASE[source] / _TO_BASE[target]
