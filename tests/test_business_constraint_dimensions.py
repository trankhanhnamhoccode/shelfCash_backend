from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.business_constraints import validate_and_normalize_business_constraint
from app.core.exceptions import BusinessConstraintError, PlanningError
from app.core.units import normalize_unit
from app.models.business import IngredientModel, InventoryConstraintModel
from app.services.business_constraint_resolver import BusinessConstraintResolver


LITER = normalize_unit("liter")


@pytest.mark.parametrize("alias", ["day", "days", "d", "ngày"])
def test_duration_aliases_are_canonical_days_without_quantity_normalization(alias, monkeypatch):
    ingredient = SimpleNamespace(base_unit=LITER)
    monkeypatch.setattr("app.core.business_constraints.normalize_unit", lambda value: pytest.fail("quantity normalizer was called"))
    result = validate_and_normalize_business_constraint("shelf_life_target", 7, alias, ingredient)
    assert (result.value, result.unit, result.dimension) == (Decimal("7"), "day", "duration")


@pytest.mark.parametrize("value", [0, -1, Decimal("1.5")])
def test_duration_requires_positive_integer_days(value):
    with pytest.raises(BusinessConstraintError) as exc:
        validate_and_normalize_business_constraint("shelf_life_target", value, "day", SimpleNamespace(base_unit=LITER))
    assert exc.value.code == "BUSINESS_CONSTRAINT_VALUE_INVALID"


def test_invalid_duration_unit_has_dimension_details():
    with pytest.raises(BusinessConstraintError) as exc:
        validate_and_normalize_business_constraint("shelf_life_target", 7, "week", SimpleNamespace(base_unit=LITER))
    assert exc.value.code == "BUSINESS_CONSTRAINT_UNIT_INVALID"
    assert exc.value.details["dimension"] == "duration" and "day" in exc.value.details["allowed_units"]


@pytest.mark.parametrize(("value", "unit", "expected"), [("0.95", "ratio", "0.95"), (0, "ratio", "0"), (1, "ratio", "1"), (95, "percent", "0.95"), (95, "%", "0.95")])
def test_ratio_values_and_percent_normalization(value, unit, expected):
    result = validate_and_normalize_business_constraint("service_level_target", value, unit)
    assert result.value == Decimal(expected) and result.unit == "ratio" and result.dimension == "ratio"


@pytest.mark.parametrize(("value", "unit"), [(-1, "ratio"), (Decimal("1.01"), "ratio"), (101, "percent"), (1, "day")])
def test_invalid_ratio_value_or_unit(value, unit):
    with pytest.raises(BusinessConstraintError) as exc:
        validate_and_normalize_business_constraint("service_level_target", value, unit)
    assert exc.value.code in {"BUSINESS_CONSTRAINT_VALUE_INVALID", "BUSINESS_CONSTRAINT_UNIT_INVALID"}


def test_quantity_and_capacity_dimension_regressions():
    liter_ingredient = SimpleNamespace(base_unit=LITER)
    piece_ingredient = SimpleNamespace(base_unit=normalize_unit("piece"))
    assert validate_and_normalize_business_constraint("safety_stock", 12, "liter", liter_ingredient).unit == LITER
    assert validate_and_normalize_business_constraint("safety_stock", 12000, "ml", liter_ingredient).unit == "ml"
    assert validate_and_normalize_business_constraint("maximum_stock", 800, "piece", piece_ingredient).unit == normalize_unit("piece")
    assert validate_and_normalize_business_constraint("maximum_storage_volume", 100, "liter").unit == LITER
    assert validate_and_normalize_business_constraint("storage_capacity", 500, "kg").unit == "kg"
    with pytest.raises(BusinessConstraintError) as exc:
        validate_and_normalize_business_constraint("safety_stock", 1, "day", liter_ingredient)
    assert exc.value.code == "BUSINESS_CONSTRAINT_UNIT_INVALID"
    with pytest.raises(BusinessConstraintError):
        validate_and_normalize_business_constraint("maximum_storage_volume", 1, "kg")


def test_store_closed_date_points_to_calendar():
    with pytest.raises(BusinessConstraintError) as exc:
        validate_and_normalize_business_constraint("store_closed_date", 1, "day")
    assert exc.value.code == "BUSINESS_CONSTRAINT_TYPE_UNSUPPORTED"
    assert exc.value.details["use_instead"] == "calendar_features.is_store_closed"


def test_resolver_is_dimension_aware(session_factory):
    with session_factory() as session:
        session.add(IngredientModel(ingredient_id="duration-ingredient", store_id="STORE_001", ingredient="Duration ingredient",
            normalized_name="duration ingredient", base_unit=LITER, active=True, source="test"))
        session.add_all([
            InventoryConstraintModel(constraint_id="duration", store_id="STORE_001", ingredient_id="duration-ingredient",
                constraint_type="shelf_life_target", value=7, unit="day", effective_date=date(2026, 7, 1), version=1, active=True, source="test"),
            InventoryConstraintModel(constraint_id="ratio", store_id="STORE_001", ingredient_id=None,
                constraint_type="service_level_target", value=Decimal("0.95"), unit="ratio", effective_date=date(2026, 7, 1), version=1, active=True, source="test"),
        ]); session.flush()
        resolver = BusinessConstraintResolver(session)
        assert resolver.resolve_duration_days("STORE_001", "shelf_life_target", "duration-ingredient", date(2026, 8, 1)) == 7
        assert resolver.resolve_ratio("STORE_001", "service_level_target", as_of_date=date(2026, 8, 1)) == Decimal("0.95")
        with pytest.raises(PlanningError) as exc:
            resolver.resolve_quantity("STORE_001", "shelf_life_target", None, "day", date(2026, 8, 1))
        assert exc.value.code == "BUSINESS_CONSTRAINT_DIMENSION_MISMATCH"
