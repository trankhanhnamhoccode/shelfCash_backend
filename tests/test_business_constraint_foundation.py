import ast
from datetime import date
from pathlib import Path

from app.core.business_constraints import CONSTRAINT_ALIAS_INDEX, CONSTRAINT_DEFINITIONS, normalize_constraint_type
from app.models.business import InventoryConstraintModel


def test_registry_has_unique_canonical_types_and_aliases():
    assert len(CONSTRAINT_DEFINITIONS) == len(set(CONSTRAINT_DEFINITIONS))
    assert all(key == definition.canonical_name for key, definition in CONSTRAINT_DEFINITIONS.items())
    assert len(CONSTRAINT_ALIAS_INDEX) == len(set(CONSTRAINT_ALIAS_INDEX))
    assert normalize_constraint_type("Safety Stock") == "safety_stock"
    assert normalize_constraint_type("max-stock") == "maximum_stock"
    assert normalize_constraint_type("shelf life") == "shelf_life_target"


def test_discovery_endpoint_exposes_registry_contract(client):
    response = client.get("/api/v1/business-constraint-types")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == len(CONSTRAINT_DEFINITIONS)
    assert len({item["constraint_type"] for item in items}) == len(items)
    shelf_life = next(item for item in items if item["constraint_type"] == "shelf_life_target")
    assert shelf_life == {
        "constraint_type": "shelf_life_target", "aliases": ["shelf life target", "shelf_life"],
        "scope": "ingredient", "dimension": "duration", "ingredient_required": True,
        "unit_required": True, "allowed_units": ["day"], "canonical_unit": "day",
        "minimum_value": 0.0, "maximum_value": None, "planner_support": "configured_only",
        "resolution_priority": None,
    }


def test_inventory_constraint_filter_normalizes_alias_and_rejects_typo(client):
    with client.app.state.session_factory() as session:
        session.add(InventoryConstraintModel(constraint_id="filter-service-level", store_id="STORE_001", ingredient_id=None,
            constraint_type="service_level_target", value="0.95", unit="ratio", effective_date=date(2026, 7, 1),
            version=1, active=True, source="test")); session.commit()
    alias = client.get("/api/v1/stores/STORE_001/inventory-constraints", params={"constraint_type": "Service Level"})
    assert alias.status_code == 200
    assert [item["constraint_type"] for item in alias.json()["items"]] == ["service_level_target"]
    invalid = client.get("/api/v1/stores/STORE_001/inventory-constraints", params={"constraint_type": "saftey_stock"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "BUSINESS_CONSTRAINT_TYPE_UNSUPPORTED"
    assert "safety_stock" in invalid.json()["details"]["supported_types"]


def test_import_service_contains_only_one_business_constraint_handler():
    tree = ast.parse(Path("app/services/business_persistence.py").read_text(encoding="utf-8"))
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ImportBusinessPersistenceService")
    handlers = [node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == "_persist_business_constraints"]
    assert len(handlers) == 1
