from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.business import (
    IngredientModel,
    InventoryLotModel,
    InventoryMovementModel,
    SupplierModel,
    SupplierIngredientTermModel,
)
from app.models.operations import ForecastRunModel, PlanRunModel, RecommendationModel


BASE = "/api/v1/stores/STORE_001"
OPENAPI_BASE = "/api/v1/stores/{store_id}"


def _seed_completion_inputs(session_factory):
    with session_factory() as session:
        session.add_all([
            IngredientModel(
                ingredient_id="contract-ing", store_id="STORE_001", ingredient="Contract ingredient",
                normalized_name="contract ingredient", base_unit="kg", active=True,
                source="test", version=1,
            ),
            SupplierModel(
                supplier_id="contract-supplier", store_id="STORE_001", supplier="Contract supplier",
                normalized_name="contract supplier", active=True, source="test",
            ),
        ])
        session.commit()


def _seed_po_plan(session_factory, *, quantity=Decimal("10"), unit_cost=100):
    forecast_id, plan_id, recommendation_id = (str(uuid4()) for _ in range(3))
    with session_factory() as session:
        session.add_all([
            SupplierIngredientTermModel(
                constraint_id=str(uuid4()), store_id="STORE_001", supplier_id="contract-supplier",
                ingredient_id="contract-ing", unit_cost=unit_cost, moq=Decimal("1"),
                pack_size=Decimal("1"), lead_time_days=1, shelf_life_days=5, unit="kg",
                version=1, active=True, source="test",
            ),
            ForecastRunModel(
                forecast_run_id=forecast_id, store_id="STORE_001", cutoff_date=date.today(),
                horizon_days=7, quantiles_json="[0.25,0.5,0.75]", scope_json="{}",
                use_latest_calendar=True, status="completed", engine_status="test_adapter",
                request_hash="contract-forecast",
            ),
            PlanRunModel(
                plan_run_id=plan_id, store_id="STORE_001", forecast_run_id=forecast_id,
                strategy="balanced", budget_limit=10000, as_of_date=date.today(),
                include_open_purchase_orders=True, status="completed", engine_status="test_adapter",
                request_hash="contract-plan", warnings_json="[]",
            ),
            RecommendationModel(
                recommendation_id=recommendation_id, plan_run_id=plan_id, store_id="STORE_001",
                ingredient_id="contract-ing", unit="kg", order_quantity=quantity,
                unit_cost=unit_cost, cost=int(quantity * unit_cost), supplier_id="contract-supplier",
                moq=Decimal("1"), pack_size=Decimal("1"), lead_time_days=1,
            ),
        ])
        session.commit()
    return plan_id, recommendation_id


def test_completion_write_response_shapes_preserve_current_contract(client, session_factory):
    _seed_completion_inputs(session_factory)
    product = client.post(f"{BASE}/products", json={
        "product": "Contract product", "sku": "CONTRACT-PRODUCT", "price": 100, "active": True,
    }).json()
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all([
            InventoryLotModel(
                lot_id="contract-lot", store_id="STORE_001", ingredient_id="contract-ing",
                received_date=date.today(), initial_quantity=Decimal("5"), unit="kg",
                source="test", version=1,
            ),
            InventoryMovementModel(
                movement_id="contract-opening", store_id="STORE_001", lot_id="contract-lot",
                movement_type="opening_balance", quantity_delta=Decimal("5"), unit="kg",
                occurred_at=now, source="test",
            ),
        ])
        session.commit()

    counted = client.post(f"{BASE}/inventory-counts", json={
        "counted_at": now.isoformat(), "lines": [{"lot_id": "contract-lot", "counted_quantity": "4", "unit": "kg"}],
    })
    assert counted.status_code == 201
    assert set(counted.json()) == {"inventory_count_id", "adjustments", "inventory"}
    assert counted.json()["adjustments"][0]["quantity_delta"] == "-1.000000"
    assert counted.json()["inventory"] == []

    adjusted = client.post(f"{BASE}/inventory-adjustments", json={
        "occurred_at": now.isoformat(), "reference": "CONTRACT-WASTE",
        "lines": [{"lot_id": "contract-lot", "expected_version": 2, "quantity_delta": "-1", "unit": "kg", "reason": "waste"}],
    })
    assert adjusted.status_code == 201
    assert set(adjusted.json()) == {"inventory_adjustment_id", "occurred_at", "movements"}
    assert adjusted.json()["movements"][0]["after_quantity"] == "3.000000"

    sales = {"source": "pos", "records": [{
        "external_record_id": "CONTRACT-SALE", "date": "2026-09-01", "product_id": product["product_id"],
        "quantity": "2", "unit_price": 100, "promotion": False,
    }]}
    sales_response = client.post(f"{BASE}/sales-history/batch", json=sales, headers={"Idempotency-Key": "contract-sales"})
    sales_replay = client.post(f"{BASE}/sales-history/batch", json=sales, headers={"Idempotency-Key": "contract-sales"})
    assert sales_response.status_code == sales_replay.status_code == 201
    assert sales_response.json() == sales_replay.json()
    assert set(sales_response.json()) == {"batch_id", "created_count", "unchanged_count", "records", "usage_rebuild", "warnings"}
    assert sales_response.json()["records"][0]["status"] == "created"

    purchases = {"source": "supplier_invoice", "inventory_effect": "record_only", "records": [{
        "external_record_id": "CONTRACT-PURCHASE", "date": "2026-09-01", "ingredient_id": "contract-ing",
        "supplier_id": "contract-supplier", "quantity": "2", "unit": "kg", "unit_cost": 30,
    }]}
    purchase_response = client.post(f"{BASE}/purchase-history/batch", json=purchases)
    assert purchase_response.status_code == 201
    assert set(purchase_response.json()) == {"batch_id", "created_count", "unchanged_count", "records", "inventory_applied"}
    assert purchase_response.json()["inventory_applied"] is False

    supplier = client.post(f"{BASE}/supplier-constraints", json={
        "ingredient_id": "contract-ing", "supplier_id": "contract-supplier", "unit_cost": 100,
        "moq": "1", "pack_size": "1", "lead_time_days": 2, "shelf_life_days": 5, "unit": "kg",
    })
    assert supplier.status_code == 201
    assert "available_delivery_days" not in supplier.json()
    updated_supplier = client.put(f"{BASE}/supplier-constraints/{supplier.json()['constraint_id']}", json={
        "ingredient_id": "contract-ing", "supplier_id": "contract-supplier", "unit_cost": 120,
        "moq": "1", "pack_size": "1", "lead_time_days": 2, "shelf_life_days": 5, "unit": "kg", "version": 1,
    })
    assert updated_supplier.status_code == 200
    assert updated_supplier.json()["version"] == 2

    settings = client.put(f"{BASE}/settings", json={
        "monthly_budget": 5000, "forecast_horizon": 7, "default_strategy": "balanced", "version": 1,
    })
    assert settings.status_code == 200
    assert settings.json()["remaining_budget"] == 5000
    calendar = client.put(f"{BASE}/calendar-features", json={
        "items": [{"date": "2026-09-02", "holiday": True, "promotion": False, "promotion_note": "ignored"}],
    })
    assert calendar.status_code == 200
    assert calendar.json()["items"] == [{"date": "2026-09-02", "weekday": "Wednesday", "weekend": False, "holiday": True, "promotion": False, "promotion_note": None}]


def test_purchase_order_response_contract_preserves_list_patch_and_receive_states(client, session_factory):
    _seed_completion_inputs(session_factory)
    assert client.put(f"{BASE}/settings", json={
        "monthly_budget": 5000, "forecast_horizon": 7, "default_strategy": "balanced", "version": 1,
    }).status_code == 200
    plan_id, recommendation_id = _seed_po_plan(session_factory)
    create = client.post(f"{BASE}/purchase-orders", json={
        "plan_run_id": plan_id, "lines": [{"recommendation_id": recommendation_id}],
    }, headers={"Idempotency-Key": "contract-po-create"})
    assert create.status_code == 201
    assert set(create.json()) == {"orders"}
    po = create.json()["orders"][0]
    assert po["status"] == "draft" and po["lines"][0]["order_quantity"] == "10.000000"

    listed = client.get(f"{BASE}/purchase-orders")
    fetched = client.get(f"{BASE}/purchase-orders/{po['po_id']}")
    assert listed.status_code == fetched.status_code == 200
    assert listed.json()["items"] == [fetched.json()]
    assert listed.json()["page"] == 1 and listed.json()["page_size"] == 50 and listed.json()["total"] == 1

    patched = client.patch(f"{BASE}/purchase-orders/{po['po_id']}", json={
        "version": 1, "line_updates": [{"po_line_id": po["lines"][0]["po_line_id"], "order_quantity": "8"}],
    })
    assert patched.status_code == 200
    assert patched.json()["total"] == 800 and patched.json()["version"] == 2

    now = datetime.now(timezone.utc)
    confirmed = client.post(f"{BASE}/purchase-orders/{po['po_id']}/confirm", json={"version": 2, "confirmed_at": now.isoformat()})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ordered" and confirmed.json()["version"] == 3
    assert client.get(f"{BASE}/settings").json()["reserved_budget"] == 800

    line = confirmed.json()["lines"][0]
    partial = client.post(f"{BASE}/purchase-orders/{po['po_id']}/receive", json={
        "version": 3, "received_at": (now + timedelta(hours=1)).isoformat(), "delivery_reference": "CONTRACT-DN-1",
        "lines": [{"po_line_id": line["po_line_id"], "lots": [{"quantity": "3", "expiry_date": (date.today() + timedelta(days=5)).isoformat(), "supplier_lot_code": "CONTRACT-LOT-1"}]}],
    })
    assert partial.status_code == 201
    assert partial.json()["status"] == "partially_received"
    assert partial.json()["lines"][0]["received_quantity"] == "3.000000"
    assert partial.json()["received_at"] is None

    complete = client.post(f"{BASE}/purchase-orders/{po['po_id']}/receive", json={
        "version": 4, "received_at": (now + timedelta(hours=2)).isoformat(), "delivery_reference": "CONTRACT-DN-2",
        "lines": [{"po_line_id": line["po_line_id"], "lots": [{"quantity": "5", "expiry_date": (date.today() + timedelta(days=5)).isoformat(), "supplier_lot_code": "CONTRACT-LOT-2"}]}],
    })
    assert complete.status_code == 201
    assert complete.json()["status"] == "received"
    assert complete.json()["lines"][0]["received_quantity"] == "8.000000"
    assert client.get(f"{BASE}/settings").json()["reserved_budget"] == 0
    assert client.get(f"{BASE}/settings").json()["spent_budget"] == 800


def test_completion_and_purchase_order_openapi_boundaries_are_explicit(client):
    schema = client.get("/openapi.json").json()
    expected = {
        (f"{OPENAPI_BASE}/inventory-counts", "post"): ("201", "InventoryCountResultResponse"),
        (f"{OPENAPI_BASE}/inventory-adjustments", "post"): ("201", "InventoryAdjustmentResultResponse"),
        (f"{OPENAPI_BASE}/sales-history/batch", "post"): ("201", "SalesBatchResultResponse"),
        (f"{OPENAPI_BASE}/purchase-history/batch", "post"): ("201", "PurchaseBatchResultResponse"),
        (f"{OPENAPI_BASE}/supplier-constraints", "post"): ("201", "SupplierTermWriteResponse"),
        (f"{OPENAPI_BASE}/supplier-constraints/{{constraint_id}}", "put"): ("200", "SupplierTermWriteResponse"),
        (f"{OPENAPI_BASE}/settings", "put"): ("200", "StoreSettingsResponse"),
        (f"{OPENAPI_BASE}/calendar-features", "put"): ("200", "CalendarWriteResultResponse"),
        (f"{OPENAPI_BASE}/purchase-orders", "post"): ("201", "PurchaseOrderCreateResponse"),
        (f"{OPENAPI_BASE}/purchase-orders", "get"): ("200", "Page_PurchaseOrderResponse_"),
        (f"{OPENAPI_BASE}/purchase-orders/{{po_id}}", "get"): ("200", "PurchaseOrderResponse"),
        (f"{OPENAPI_BASE}/purchase-orders/{{po_id}}", "patch"): ("200", "PurchaseOrderResponse"),
        (f"{OPENAPI_BASE}/purchase-orders/{{po_id}}/confirm", "post"): ("200", "PurchaseOrderResponse"),
        (f"{OPENAPI_BASE}/purchase-orders/{{po_id}}/receive", "post"): ("201", "PurchaseOrderResponse"),
    }
    for (path, method), (status, model) in expected.items():
        response = schema["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert response == {"$ref": f"#/components/schemas/{model}"}

    assert schema["paths"][f"{OPENAPI_BASE}/bootstrap"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {}
    assert schema["paths"][f"{OPENAPI_BASE}/dashboard"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {}
    assert schema["paths"]["/api/v1/stores/{store_id}/menu"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/MenuResponse"}
    assert schema["paths"][f"{OPENAPI_BASE}/inventory"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Page_InventoryLotResponse_"}
    assert len(schema["paths"]) == 58
    assert sum(len(item) for item in schema["paths"].values()) == 70
