from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.business import (
    CalendarFeatureModel,
    IngredientModel,
    InventoryLotModel,
    InventoryMovementModel,
    ProductModel,
    PurchaseReceiptModel,
    RecipeLineModel,
    RecipeVersionModel,
    SalesDailyModel,
    SupplierModel,
    UsageDailyModel,
)
from app.models.import_normalized import ImportJobModel


BASE = "/api/v1/stores/STORE_001"
OPENAPI_BASE = "/api/v1/stores/{store_id}"
NOW = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)
WIRE_NOW = NOW.replace(tzinfo=None).isoformat()


def _seed_operational_page_records(session_factory):
    with session_factory() as session:
        session.add_all([
            IngredientModel(
                ingredient_id="page-ing", store_id="STORE_001", ingredient="Page ingredient",
                normalized_name="page ingredient", sku="PAGE-ING", base_unit="kg",
                active=True, source="test", version=1,
            ),
            SupplierModel(
                supplier_id="page-supplier", store_id="STORE_001", supplier="Page supplier",
                normalized_name="page supplier", active=True, source="test",
            ),
            ProductModel(
                product_id="page-product", store_id="STORE_001", product="Page product",
                normalized_name="page product", sku="PAGE-PRODUCT", price=25000,
                item_type="single", active=True, source="test", version=1,
            ),
            ImportJobModel(
                import_id="page-import", store_id="STORE_001", forecast_date=date(2026, 9, 1),
                forecast_horizon=7, status="completed", requires_review=False,
                created_at=NOW, completed_at=NOW + timedelta(minutes=1),
            ),
            RecipeVersionModel(
                recipe_version_id="page-recipe", store_id="STORE_001", product_id="page-product",
                version=1, effective_from=date(2026, 9, 1), content_hash="recipe-hash",
                source="test", created_at=NOW,
            ),
            SalesDailyModel(
                sales_record_id="page-sale", store_id="STORE_001", date=date(2026, 9, 3),
                product_id="page-product", quantity=Decimal("2.500000"), unit_price=25000,
                promotion=False, is_stockout=None, source="pos", import_id="page-import",
                external_record_id="SALE-EXT", created_at=NOW, updated_at=NOW,
            ),
            UsageDailyModel(
                usage_record_id="page-usage", store_id="STORE_001", date=date(2026, 9, 3),
                ingredient_id="page-ing", quantity=Decimal("1.250000"), unit="kg", source="derived",
                usage_source="recipe", waste_quantity=Decimal("0.050000"), import_id="page-import",
                created_at=NOW, updated_at=NOW,
            ),
            PurchaseReceiptModel(
                receipt_id="page-purchase", store_id="STORE_001", ingredient_id="page-ing",
                supplier_id="page-supplier", receipt_date=date(2026, 9, 2),
                quantity=Decimal("5.500000"), unit="kg", unit_cost=10000,
                total_cost=Decimal("55000.00"), purchase_order_id="legacy-po",
                expiry_date=date(2026, 9, 20), batch_code="BATCH-1", source="invoice",
                import_id="page-import", created_at=NOW, external_record_id="PURCHASE-EXT",
                inventory_effect="record_only", po_id="page-po", po_line_id="page-po-line",
            ),
            InventoryLotModel(
                lot_id="page-lot", store_id="STORE_001", ingredient_id="page-ing",
                supplier_id="page-supplier", batch_code="LOT-1", received_date=date(2026, 9, 1),
                received_date_status="declared", expiry_date=date.today() + timedelta(days=30),
                initial_quantity=Decimal("8.250000"), unit="kg", unit_cost=10000,
                source="test", snapshot_date=date(2026, 9, 3), version=2,
            ),
            CalendarFeatureModel(
                calendar_feature_id="page-calendar", store_id="STORE_001", date=date(2026, 9, 4),
                is_weekend=False, is_holiday=True, is_store_closed=False, is_promotion=True,
                promotion_name="Page promotion", source="test",
            ),
        ])
        session.flush()
        session.add_all([
            RecipeLineModel(
                recipe_line_id="page-recipe-line", recipe_version_id="page-recipe",
                ingredient_id="page-ing", quantity=Decimal("0.500000"), unit="kg",
            ),
            InventoryMovementModel(
                movement_id="page-movement", store_id="STORE_001", lot_id="page-lot",
                movement_type="opening_balance", quantity_delta=Decimal("8.250000"), unit="kg",
                occurred_at=NOW, source="test", source_id="movement-source", source_import_id="page-import",
                note="Initial page contract balance", created_at=NOW,
            ),
        ])
        session.commit()


def _assert_page(body, *, total=1):
    assert set(body) == {"items", "page", "page_size", "total"}
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert body["total"] == total


def test_operational_page_responses_preserve_current_item_shapes(client, session_factory):
    _seed_operational_page_records(session_factory)

    imports = client.get(f"{BASE}/imports").json()
    _assert_page(imports)
    assert imports["items"] == [{
        "import_id": "page-import", "store_id": "STORE_001", "status": "completed",
        "forecast_date": "2026-09-01", "forecast_horizon": 7, "requires_review": False,
        "file_count": 0, "profile_count": 0, "warning_count": 0, "error_count": 0,
        "created_at": WIRE_NOW,
        "completed_at": (NOW + timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        "failed_at": None,
    }]

    recipes = client.get(f"{BASE}/products/page-product/recipe-versions").json()
    _assert_page(recipes)
    assert recipes["items"][0] == {
        "recipe_version_id": "page-recipe", "version": 1, "effective_from": "2026-09-01",
        "effective_to": None, "content_hash": "recipe-hash", "created_at": WIRE_NOW,
        "lines": [{"recipe_line_id": "page-recipe-line", "ingredient_id": "page-ing",
                   "ingredient": "Page ingredient", "quantity": "0.500000", "unit": "kg"}],
    }

    sales = client.get(f"{BASE}/sales-history").json()
    _assert_page(sales)
    assert sales["items"][0]["quantity"] == 2.5
    assert set(sales["items"][0]) == {
        "sales_record_id", "store_id", "date", "product_id", "quantity", "unit_price",
        "promotion", "is_stockout", "source", "import_id", "created_at", "updated_at",
        "external_record_id",
    }

    usage = client.get(f"{BASE}/usage-history").json()
    _assert_page(usage)
    assert usage["items"][0]["quantity"] == 1.25
    assert usage["items"][0]["waste_quantity"] == 0.05

    purchases = client.get(f"{BASE}/purchase-history").json()
    _assert_page(purchases)
    assert purchases["items"][0]["quantity"] == 5.5
    assert purchases["items"][0]["total_cost"] == 55000.0
    assert purchases["items"][0]["po_line_id"] == "page-po-line"

    inventory = client.get(f"{BASE}/inventory").json()
    _assert_page(inventory)
    assert inventory["items"][0]["on_hand"] == 8.25
    assert inventory["items"][0]["usable_quantity"] == 8.25
    assert inventory["items"][0]["status"] == "healthy"

    movements = client.get(f"{BASE}/inventory-movements").json()
    _assert_page(movements)
    assert movements["items"][0]["quantity_delta"] == 8.25
    assert movements["items"][0]["source_import_id"] == "page-import"
    assert "source_row_hash" not in movements["items"][0]

    calendar = client.get(f"{BASE}/calendar-features").json()
    _assert_page(calendar)
    assert calendar["items"] == [{
        "date": "2026-09-04", "weekday": "Friday", "weekend": False,
        "holiday": True, "promotion": True, "promotion_note": "Page promotion",
    }]


def test_operational_page_pagination_filters_and_empty_behavior_are_unchanged(client, session_factory):
    _seed_operational_page_records(session_factory)

    beyond = client.get(f"{BASE}/sales-history?page=2&page_size=1")
    assert beyond.status_code == 200
    assert beyond.json() == {"items": [], "page": 2, "page_size": 1, "total": 1}

    empty = client.get(f"{BASE}/purchase-history?source=missing")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "page": 1, "page_size": 50, "total": 0}

    filtered = client.get(f"{BASE}/inventory?ingredient_id=page-ing")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1

    invalid = client.get(f"{BASE}/calendar-features?page=0")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"


def test_operational_page_openapi_contracts_are_explicit_and_preserve_prior_r1_boundaries(client):
    schema = client.get("/openapi.json").json()
    expected = {
        f"{OPENAPI_BASE}/imports": "Page_ImportSummaryResponse_",
        f"{OPENAPI_BASE}/products/{{product_id}}/recipe-versions": "Page_RecipeVersionSummaryResponse_",
        f"{OPENAPI_BASE}/sales-history": "Page_SalesHistoryRecordResponse_",
        f"{OPENAPI_BASE}/usage-history": "Page_UsageHistoryRecordResponse_",
        f"{OPENAPI_BASE}/purchase-history": "Page_PurchaseHistoryRecordResponse_",
        f"{OPENAPI_BASE}/inventory": "Page_InventoryLotResponse_",
        f"{OPENAPI_BASE}/inventory-movements": "Page_InventoryMovementResponse_",
        f"{OPENAPI_BASE}/calendar-features": "Page_CalendarFeatureResponse_",
    }
    for path, model in expected.items():
        response_schema = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{model}"}
        assert schema["components"]["schemas"][model]["properties"]["items"]["type"] == "array"

    assert schema["paths"][f"{OPENAPI_BASE}/purchase-orders"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Page_PurchaseOrderResponse_"}
    assert schema["paths"]["/api/v1/stores/{store_id}/decision-runs"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/DecisionPackage"}
    assert schema["paths"]["/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/IngredientDemandRunResponse"}
    assert schema["paths"]["/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ProcurementPlanRunResponse"}
    assert schema["paths"]["/api/v1/stores/{store_id}/menu"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/MenuResponse"}
    assert len(schema["paths"]) == 59
    assert sum(len(methods) for methods in schema["paths"].values()) == 71
