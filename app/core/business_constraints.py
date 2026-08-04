from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import unicodedata

from app.core.exceptions import BusinessConstraintError
from app.core.units import normalize_unit, unit_dimension, validate_compatible


@dataclass(frozen=True)
class ConstraintDefinition:
    dimension: str
    scope: str
    canonical_unit: str | None = None
    allowed_units: tuple[str, ...] = ()
    minimum: Decimal = Decimal("0")
    maximum: Decimal | None = None
    integer_value: bool = False


@dataclass(frozen=True)
class NormalizedBusinessConstraint:
    constraint_type: str
    value: Decimal
    unit: str | None
    dimension: str
    scope: str


CONSTRAINT_DEFINITIONS = {
    "safety_stock": ConstraintDefinition("quantity", "ingredient"),
    "maximum_stock": ConstraintDefinition("quantity", "ingredient"),
    "minimum_stock": ConstraintDefinition("quantity", "ingredient"),
    "reorder_point": ConstraintDefinition("quantity", "ingredient"),
    "shelf_life_target": ConstraintDefinition("duration", "ingredient", "day", ("day",), Decimal("0"), integer_value=True),
    "service_level_target": ConstraintDefinition("ratio", "store_or_ingredient", "ratio", ("ratio",), Decimal("0")),
    "storage_capacity": ConstraintDefinition("quantity_or_capacity", "store"),
    "warehouse_capacity": ConstraintDefinition("quantity_or_capacity", "store"),
    "maximum_storage_volume": ConstraintDefinition("volume", "store"),
    "budget": ConstraintDefinition("currency", "store", "VND", ("VND",)),
}

_DURATION_UNITS = {"day": "day", "days": "day", "d": "day", "ngay": "day"}
_RATIO_UNITS = {"ratio": ("ratio", Decimal("1")), "percent": ("ratio", Decimal("0.01")),
                "percentage": ("ratio", Decimal("0.01")), "%": ("ratio", Decimal("0.01"))}
_CURRENCY_UNITS = {"vnd": "VND", "đ": "VND", "dong": "VND"}


def _fold(value) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")


def normalize_constraint_type(value) -> str:
    return _fold(value).replace(" ", "_").replace("-", "_")


def constraint_definition(constraint_type) -> tuple[str, ConstraintDefinition]:
    kind = normalize_constraint_type(constraint_type)
    definition = CONSTRAINT_DEFINITIONS.get(kind)
    if definition is None:
        details = {"constraint_type": kind, "supported_types": sorted(CONSTRAINT_DEFINITIONS)}
        if kind == "store_closed_date":
            details["use_instead"] = "calendar_features.is_store_closed"
        raise BusinessConstraintError("BUSINESS_CONSTRAINT_TYPE_UNSUPPORTED", "Business constraint type is not supported.", details)
    return kind, definition


def _decimal(value, kind, definition) -> Decimal:
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise BusinessConstraintError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Business constraint value is invalid.",
            {"constraint_type": kind, "dimension": definition.dimension, "value": value}) from None
    if number < definition.minimum or (definition.maximum is not None and number > definition.maximum):
        raise BusinessConstraintError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Business constraint value is outside its allowed range.",
            {"constraint_type": kind, "dimension": definition.dimension, "value": str(number),
             "minimum": str(definition.minimum), "maximum": str(definition.maximum) if definition.maximum is not None else None})
    if definition.integer_value and (number <= 0 or number != number.to_integral_value()):
        raise BusinessConstraintError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Business constraint requires a positive integer value.",
            {"constraint_type": kind, "dimension": definition.dimension, "value": str(number)})
    return number


def _unit_error(kind, definition, unit, allowed_units=None):
    raise BusinessConstraintError("BUSINESS_CONSTRAINT_UNIT_INVALID", "Business constraint unit is invalid.", {
        "constraint_type": kind, "dimension": definition.dimension, "unit": unit,
        "allowed_units": list(allowed_units or definition.allowed_units),
    })


def validate_and_normalize_business_constraint(constraint_type, value, unit, ingredient=None) -> NormalizedBusinessConstraint:
    kind, definition = constraint_definition(constraint_type)
    if definition.scope == "ingredient" and ingredient is None:
        raise BusinessConstraintError("INGREDIENT_NOT_FOUND", "Ingredient is required for this business constraint.", {"constraint_type": kind})
    if definition.scope == "store" and ingredient is not None:
        raise BusinessConstraintError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Store-level constraint cannot target an ingredient.", {"constraint_type": kind})

    number = _decimal(value, kind, definition)
    raw_unit = str(unit or "").strip()
    try:
        if definition.dimension == "quantity":
            if not raw_unit:
                _unit_error(kind, definition, unit, [ingredient.base_unit] if ingredient else [])
            canonical_unit = normalize_unit(raw_unit)
            validate_compatible(canonical_unit, ingredient.base_unit)
        elif definition.dimension == "duration":
            canonical_unit = _DURATION_UNITS.get(_fold(raw_unit))
            if canonical_unit is None:
                _unit_error(kind, definition, unit, sorted(_DURATION_UNITS))
        elif definition.dimension == "ratio":
            normalized = _RATIO_UNITS.get(_fold(raw_unit))
            if normalized is None:
                _unit_error(kind, definition, unit, sorted(_RATIO_UNITS))
            canonical_unit, multiplier = normalized
            number *= multiplier
            if number < 0 or number > 1:
                raise BusinessConstraintError("BUSINESS_CONSTRAINT_VALUE_INVALID", "Ratio must be between 0 and 1.",
                    {"constraint_type": kind, "dimension": "ratio", "value": str(number), "unit": canonical_unit})
        elif definition.dimension in {"quantity_or_capacity", "volume"}:
            if not raw_unit:
                _unit_error(kind, definition, unit, ["kg", "g", "liter", "ml", "piece"])
            canonical_unit = normalize_unit(raw_unit)
            if definition.dimension == "volume" and unit_dimension(canonical_unit) != "volume":
                _unit_error(kind, definition, unit, ["liter", "ml"])
        elif definition.dimension == "currency":
            canonical_unit = _CURRENCY_UNITS.get(_fold(raw_unit or definition.canonical_unit))
            if canonical_unit is None:
                _unit_error(kind, definition, unit, sorted(_CURRENCY_UNITS))
        else:
            raise AssertionError(f"Unhandled constraint dimension: {definition.dimension}")
    except BusinessConstraintError:
        raise
    except Exception as exc:
        allowed = {
            "quantity": [ingredient.base_unit] if ingredient else [],
            "quantity_or_capacity": ["kg", "g", "liter", "ml", "piece"],
            "volume": ["liter", "ml"],
            "duration": sorted(_DURATION_UNITS),
            "ratio": sorted(_RATIO_UNITS),
            "currency": sorted(_CURRENCY_UNITS),
        }.get(definition.dimension, [])
        _unit_error(kind, definition, unit, allowed)
        raise AssertionError from exc
    return NormalizedBusinessConstraint(kind, number, canonical_unit, definition.dimension, definition.scope)
