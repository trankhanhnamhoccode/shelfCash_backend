from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import unicodedata

from app.core.exceptions import BusinessConstraintError
from app.core.units import normalize_unit, unit_dimension, validate_compatible


@dataclass(frozen=True)
class ConstraintDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    dimension: str
    scope: str
    ingredient_required: bool
    unit_required: bool
    allowed_units: tuple[str, ...]
    canonical_unit: str | None = None
    minimum: Decimal = Decimal("0")
    maximum: Decimal | None = None
    integer_value: bool = False
    planner_support: str = "configured_only"
    resolution_priority: int | None = None


@dataclass(frozen=True)
class NormalizedBusinessConstraint:
    constraint_type: str
    value: Decimal
    unit: str | None
    dimension: str
    scope: str


PHYSICAL_UNITS = ("kg", "g", "lít", "ml", "cái")
VOLUME_UNITS = ("lít", "ml")


def _definition(name, aliases, dimension, scope, ingredient_required, unit_required, allowed_units,
                canonical_unit=None, minimum=Decimal("0"), maximum=None, integer_value=False,
                planner_support="configured_only", resolution_priority=None):
    return ConstraintDefinition(name, tuple(aliases), dimension, scope, ingredient_required, unit_required,
        tuple(allowed_units), canonical_unit, minimum, maximum, integer_value, planner_support, resolution_priority)


CONSTRAINT_DEFINITIONS = {
    "safety_stock": _definition("safety_stock", ("safety stock",), "quantity", "ingredient", True, True, PHYSICAL_UNITS, planner_support="supported"),
    "maximum_stock": _definition("maximum_stock", ("maximum stock", "max_stock"), "quantity", "ingredient", True, True, PHYSICAL_UNITS, planner_support="supported"),
    "minimum_stock": _definition("minimum_stock", ("minimum stock", "min_stock"), "quantity", "ingredient", True, True, PHYSICAL_UNITS),
    "reorder_point": _definition("reorder_point", ("reorder point",), "quantity", "ingredient", True, True, PHYSICAL_UNITS),
    "shelf_life_target": _definition("shelf_life_target", ("shelf life target", "shelf_life"), "duration", "ingredient", True, True, ("day",), "day", integer_value=True),
    "service_level_target": _definition("service_level_target", ("service level target", "service_level"), "ratio", "store_or_ingredient", False, True, ("ratio", "percent"), "ratio"),
    "storage_capacity": _definition("storage_capacity", ("storage capacity",), "capacity", "store", False, True, PHYSICAL_UNITS, resolution_priority=2),
    "warehouse_capacity": _definition("warehouse_capacity", ("warehouse capacity",), "capacity", "store", False, True, PHYSICAL_UNITS, resolution_priority=3),
    "maximum_storage_volume": _definition("maximum_storage_volume", ("maximum storage volume", "max_storage_volume"), "capacity", "store", False, True, VOLUME_UNITS, "lít", resolution_priority=1),
    "budget": _definition("budget", ("store_budget",), "currency", "store", False, True, ("VND",), "VND"),
}

_DURATION_UNITS = {"day": "day", "days": "day", "d": "day", "ngay": "day"}
_RATIO_UNITS = {"ratio": ("ratio", Decimal("1")), "percent": ("ratio", Decimal("0.01")),
                "percentage": ("ratio", Decimal("0.01")), "%": ("ratio", Decimal("0.01"))}
_CURRENCY_UNITS = {"vnd": "VND", "đ": "VND", "dong": "VND"}


def _fold(value) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")


def normalize_constraint_type(value) -> str:
    normalized = _fold(value).replace(" ", "_").replace("-", "_")
    return CONSTRAINT_ALIAS_INDEX.get(normalized, normalized)


def _build_alias_index():
    result = {}
    for canonical, definition in CONSTRAINT_DEFINITIONS.items():
        if definition.canonical_name != canonical:
            raise RuntimeError(f"Constraint registry key mismatch: {canonical}")
        for raw in (canonical, *definition.aliases):
            alias = _fold(raw).replace(" ", "_").replace("-", "_")
            previous = result.get(alias)
            if previous is not None and previous != canonical:
                raise RuntimeError(f"Duplicate business constraint alias: {raw}")
            result[alias] = canonical
    return result


CONSTRAINT_ALIAS_INDEX = _build_alias_index()


def constraint_type_catalog():
    return [{
        "constraint_type": item.canonical_name,
        "aliases": list(item.aliases),
        "scope": item.scope,
        "dimension": item.dimension,
        "ingredient_required": item.ingredient_required,
        "unit_required": item.unit_required,
        "allowed_units": list(item.allowed_units),
        "canonical_unit": item.canonical_unit,
        "minimum_value": item.minimum,
        "maximum_value": item.maximum,
        "planner_support": item.planner_support,
        "resolution_priority": item.resolution_priority,
    } for item in CONSTRAINT_DEFINITIONS.values()]


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
    if definition.ingredient_required and ingredient is None:
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
        elif definition.dimension == "capacity":
            if not raw_unit:
                _unit_error(kind, definition, unit, ["kg", "g", "liter", "ml", "piece"])
            canonical_unit = normalize_unit(raw_unit)
            if definition.canonical_unit == "lít" and unit_dimension(canonical_unit) != "volume":
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
            "capacity": list(definition.allowed_units),
            "duration": sorted(_DURATION_UNITS),
            "ratio": sorted(_RATIO_UNITS),
            "currency": sorted(_CURRENCY_UNITS),
        }.get(definition.dimension, [])
        _unit_error(kind, definition, unit, allowed)
        raise AssertionError from exc
    return NormalizedBusinessConstraint(kind, number, canonical_unit, definition.dimension, definition.scope)
