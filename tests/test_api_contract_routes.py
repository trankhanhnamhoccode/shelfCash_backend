import json
from collections import Counter
from pathlib import Path


GROUP_COUNTS = {
    "health_llm": 3,
    "import": 7,
    "bootstrap_dashboard": 2,
    "inventory": 4,
    "catalog_recipe": 11,
    "history": 5,
    "supplier_alias_settings_calendar": 9,
    "forecast_plan_po": 12,
    "forecast_core": 3,
    "demand_planning": 4,
}

EXPECTED_IMPLEMENTED = {
    ("GET", "/health"),
    ("GET", "/api/v1/llm/health"),
    ("POST", "/api/v1/llm/map-sheet"),
    ("POST", "/api/v1/imports"),
    ("GET", "/api/v1/imports/{import_id}"),
    ("POST", "/api/v1/imports/{import_id}/confirm"),
    ("POST", "/api/v1/imports/{import_id}/process"),
    ("GET", "/api/v1/imports/{import_id}/result"),
    ("GET", "/api/v1/stores/{store_id}/ingredients"),
    ("POST", "/api/v1/stores/{store_id}/ingredients"),
    ("PATCH", "/api/v1/stores/{store_id}/ingredients/{ingredient_id}"),
    ("GET", "/api/v1/stores/{store_id}/products"),
    ("POST", "/api/v1/stores/{store_id}/products"),
    ("PATCH", "/api/v1/stores/{store_id}/products/{product_id}"),
    ("GET", "/api/v1/stores/{store_id}/products/{product_id}/recipe"),
    ("PUT", "/api/v1/stores/{store_id}/products/{product_id}/recipe"),
    ("GET", "/api/v1/stores/{store_id}/aliases"),
    ("PUT", "/api/v1/stores/{store_id}/aliases"),
    ("GET", "/api/v1/stores/{store_id}/imports"),
    ("GET", "/api/v1/stores/{store_id}/products/{product_id}/recipe-versions"),
    ("GET", "/api/v1/stores/{store_id}/sales-history"),
    ("GET", "/api/v1/stores/{store_id}/usage-history"),
    ("GET", "/api/v1/stores/{store_id}/purchase-history"),
    ("GET", "/api/v1/stores/{store_id}/inventory"),
    ("GET", "/api/v1/stores/{store_id}/inventory-movements"),
    ("GET", "/api/v1/stores/{store_id}/settings"),
    ("GET", "/api/v1/stores/{store_id}/calendar-features"),
}


def test_contract_manifest_has_exact_unique_60_operations():
    entries = json.loads(
        Path("docs/API_CONTRACT_ROUTE_MANIFEST.json").read_text(encoding="utf-8")
    )
    pairs = {(entry["method"], entry["path"]) for entry in entries}
    assert len(entries) == len(pairs) == 60
    assert Counter(entry["group"] for entry in entries) == Counter(GROUP_COUNTS)


def test_application_routes_are_exact_contract_subset(client):
    manifest = json.loads(
        Path("docs/API_CONTRACT_ROUTE_MANIFEST.json").read_text(encoding="utf-8")
    )
    contract = {(entry["method"], entry["path"]) for entry in manifest}
    schema = client.get("/openapi.json").json()
    actual = {
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        for method in methods
    }
    assert actual <= contract, f"Unexpected operations: {sorted(actual - contract)}"
    assert len(actual) == 60
    assert actual == contract, (
        f"Missing: {sorted(contract - actual)}; "
        f"unexpected: {sorted(actual - contract)}"
    )
