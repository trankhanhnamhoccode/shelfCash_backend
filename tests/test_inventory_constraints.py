from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import PlanningError
from app.models.business import IngredientModel, InventoryConstraintModel, SupplierIngredientTermModel, SupplierModel
from app.services.business_constraint_resolver import BusinessConstraintResolver
from app.services.procurement_planning_service import ProcurementPlanningService


def add_catalog(session):
    session.add(IngredientModel(ingredient_id="milk", store_id="STORE_001", ingredient="Milk", normalized_name="milk", base_unit="lít", active=True, source="test"))
    for supplier_id in ("supplier-a", "supplier-b"):
        session.add(SupplierModel(supplier_id=supplier_id, store_id="STORE_001", supplier=supplier_id, normalized_name=supplier_id, active=True, source="test"))
        session.add(SupplierIngredientTermModel(constraint_id=f"term-{supplier_id}", store_id="STORE_001", supplier_id=supplier_id,
            ingredient_id="milk", unit_cost=10, moq=Decimal("1"), pack_size=Decimal("1"), lead_time_days=0,
            unit="lít", version=1, active=True, source="test"))


def constraint(constraint_id, value, unit="lít", version=1, active=True, effective=date(2026, 7, 1), end=None):
    return InventoryConstraintModel(constraint_id=constraint_id, store_id="STORE_001", ingredient_id="milk",
        constraint_type="safety_stock", value=Decimal(value), unit=unit, effective_date=effective, end_date=end,
        version=version, active=active, source="test")


def test_supplier_api_excludes_inventory_owned_fields_and_inventory_api_lists_them(client):
    with client.app.state.session_factory() as session:
        add_catalog(session); session.add(constraint("safety", "12000", "ml")); session.commit()
    response = client.get("/api/v1/stores/STORE_001/supplier-constraints")
    assert response.status_code == 200 and len(response.json()["items"]) == 2
    assert all("safety_stock" not in item and "capacity" not in item for item in response.json()["items"])
    inventory = client.get("/api/v1/stores/STORE_001/inventory-constraints", params={"constraint_type": "safety_stock", "ingredient_id": "milk", "as_of_date": "2026-08-03"})
    assert inventory.status_code == 200
    assert inventory.json()["items"][0] | {"value": "12000.000000", "unit": "ml"} == inventory.json()["items"][0]


def test_resolver_missing_zero_conversion_effective_date_and_ambiguity(session_factory):
    with session_factory() as session:
        add_catalog(session); session.flush(); resolver = BusinessConstraintResolver(session)
        assert resolver.resolve_constraint("STORE_001", "safety_stock", "milk", date(2026, 8, 3)) is None
        session.add(constraint("old", "0", effective=date(2026, 7, 1), end=date(2026, 7, 31), active=False))
        session.add(constraint("current", "12000", "ml", version=2, effective=date(2026, 8, 1))); session.flush()
        assert resolver.resolve_quantity("STORE_001", "safety_stock", "milk", "lít", date(2026, 8, 3)) == Decimal("12")
        assert resolver.resolve_quantity("STORE_001", "safety_stock", "milk", "lít", date(2026, 7, 15)) == Decimal("0")
        session.add(constraint("overlap", "1", version=3, effective=date(2026, 8, 1))); session.flush()
        with pytest.raises(PlanningError) as exc:
            resolver.resolve_constraint("STORE_001", "safety_stock", "milk", date(2026, 8, 3))
        assert exc.value.code == "BUSINESS_CONSTRAINT_AMBIGUOUS"


def test_planner_distinguishes_missing_from_configured_zero_with_multiple_suppliers(session_factory):
    forecast = SimpleNamespace(cutoff_date=date(2026, 8, 3))
    demand = [SimpleNamespace(ingredient_id="milk", target_date=date(2026, 8, 4), unit="lít", p25=Decimal("1"), p50=Decimal("1"), p75=Decimal("1"))]
    with session_factory() as session:
        add_catalog(session); session.flush()
        missing, _ = ProcurementPlanningService(session).build("STORE_001", forecast, demand, ["balanced"], False, 1000)
        assert "SAFETY_STOCK_NOT_CONFIGURED" in missing[0]["warnings"]
        trace=missing[0]["constraint_trace"]["milk"]
        assert {key:trace[key] for key in ("configured_safety_stock","effective_safety_stock","fallback_policy","maximum_stock","minimum_stock","unit")} == {"configured_safety_stock": None, "effective_safety_stock": "0", "fallback_policy": "ZERO_WITH_WARNING", "maximum_stock": None, "minimum_stock": None, "unit": "lít"}
        assert trace["target_ending_inventory"] == "0" and trace["target_ending_policy"] == "MAX_SAFETY_AND_MINIMUM"
        session.add(constraint("zero", "0")); session.flush()
        configured, _ = ProcurementPlanningService(session).build("STORE_001", forecast, demand, ["balanced"], False, 1000)
        assert "SAFETY_STOCK_NOT_CONFIGURED" not in configured[0]["warnings"]
        assert configured[0]["constraint_trace"]["milk"]["configured_safety_stock"] == "0.000000"
        assert configured[0]["constraint_trace"]["milk"]["fallback_policy"] is None
