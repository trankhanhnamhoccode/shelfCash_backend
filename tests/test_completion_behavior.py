from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
import pytest

from app.models.business import (
    IngredientModel, InventoryLotModel, InventoryMovementModel, ProductModel,
    PurchaseReceiptModel, SalesDailyModel, SupplierModel, SupplierIngredientTermModel, UsageDailyModel,
)
from app.models.operations import (
    BudgetPeriodModel, ForecastRunModel, PlanRunModel, RecommendationModel,
    PurchaseOrderModel, PurchaseOrderLineModel,
)


def _ingredient(session_factory, store="STORE_001", ident="ing-1", unit="kg"):
    with session_factory() as s:
        s.add(IngredientModel(ingredient_id=ident, store_id=store, ingredient=ident,
                              normalized_name=ident, base_unit=unit, active=True,
                              source="manual", version=1))
        s.commit()


def _supplier(session_factory, store="STORE_001", ident="sup-1"):
    with session_factory() as s:
        s.add(SupplierModel(supplier_id=ident, store_id=store, supplier=ident,
                            normalized_name=ident, active=True, source="manual"))
        s.commit()


def test_bootstrap_expired_lot_and_calendar_horizon_are_consistent(client, session_factory):
    _ingredient(session_factory, ident="expiry-ing")
    with session_factory() as s:
        lot = InventoryLotModel(
            lot_id="expired-lot", store_id="STORE_001", ingredient_id="expiry-ing",
            received_date=date.today() - timedelta(days=10), expiry_date=date.today() - timedelta(days=1),
            initial_quantity=Decimal("5"), unit="kg", source="test", version=1,
        )
        s.add(lot)
        s.add(InventoryMovementModel(
            movement_id="expired-move", store_id="STORE_001", lot_id="expired-lot",
            movement_type="opening_balance", quantity_delta=Decimal("5"), unit="kg",
            occurred_at=datetime.now(timezone.utc), source="test",
        ))
        s.commit()
    response = client.get("/api/v1/stores/STORE_001/bootstrap")
    assert response.status_code == 200
    body = response.json()
    item = next(row for row in body["inventory"] if row["lot_id"] == "expired-lot")
    assert item["status"] == "expired"
    assert item["usable_quantity"] == 0
    assert item["expiring_quantity"] == 0
    assert item["expired_quantity"] == 5
    assert len(body["future_calendar"]) >= body["settings"]["forecast_horizon"]


def test_history_batches_are_normalized_and_have_no_stock_side_effect(client, session_factory):
    product = client.post("/api/v1/stores/STORE_001/products", json={
        "product": "Drink", "sku": "DRINK", "price": 12000, "active": True
    }).json()
    sales = {"source": "pos", "records": [{
        "external_record_id": "POS-1", "date": "2026-07-27",
        "product_id": product["product_id"], "quantity": "2",
        "unit_price": 12000, "promotion": False,
    }]}
    first = client.post("/api/v1/stores/STORE_001/sales-history/batch",
                        json=sales, headers={"Idempotency-Key": "sales-1"})
    replay = client.post("/api/v1/stores/STORE_001/sales-history/batch",
                         json=sales, headers={"Idempotency-Key": "sales-1"})
    assert first.status_code == replay.status_code == 201
    assert first.json()["created_count"] == 1
    assert first.json()["usage_rebuild"]["warning_count"] == 1
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(SalesDailyModel)) == 1
        assert s.scalar(select(func.count()).select_from(UsageDailyModel)) == 0
        assert s.scalar(select(func.count()).select_from(InventoryMovementModel)) == 0

    _ingredient(session_factory, unit="kg")
    _supplier(session_factory)
    purchase = {"source": "supplier_invoice", "inventory_effect": "record_only",
                "records": [{"external_record_id": "INV-1", "date": "2026-07-25",
                             "ingredient_id": "ing-1", "supplier_id": "sup-1",
                             "quantity": "1000", "unit": "g", "unit_cost": 32000,
                             "expiry_date": "2026-08-03", "supplier_lot_code": "B1"}]}
    response = client.post("/api/v1/stores/STORE_001/purchase-history/batch",
                           json=purchase, headers={"Idempotency-Key": "purchase-1"})
    assert response.status_code == 201
    assert response.json()["inventory_applied"] is False
    with session_factory() as s:
        receipt = s.scalar(select(PurchaseReceiptModel))
        assert receipt.quantity == Decimal("1")
        assert receipt.inventory_effect == "record_only"
        assert s.scalar(select(func.count()).select_from(InventoryLotModel)) == 0


def test_history_identity_distinguishes_sales_promotion_and_purchase_null_external_id(client, session_factory):
    product = client.post("/api/v1/stores/STORE_001/products", json={"product": "Promo drink", "sku": "PROMO-DRINK", "price": 100, "active": True}).json()
    sales = {"source": "pos", "records": [
        {"external_record_id": "POS-NONE", "date": "2026-07-27", "product_id": product["product_id"], "quantity": "1", "unit_price": 100, "promotion": False},
        {"external_record_id": "POS-PROMO", "date": "2026-07-27", "product_id": product["product_id"], "quantity": "1", "unit_price": 100, "promotion": True},
    ]}
    assert client.post("/api/v1/stores/STORE_001/sales-history/batch", json=sales).status_code == 201
    _ingredient(session_factory); _supplier(session_factory)
    purchases = {"source": "manual", "inventory_effect": "record_only", "records": [
        {"external_record_id": None, "date": "2026-07-27", "ingredient_id": "ing-1", "supplier_id": "sup-1", "quantity": "1", "unit": "kg", "unit_cost": 10},
        {"external_record_id": None, "date": "2026-07-28", "ingredient_id": "ing-1", "supplier_id": "sup-1", "quantity": "2", "unit": "kg", "unit_cost": 10},
    ]}
    response = client.post("/api/v1/stores/STORE_001/purchase-history/batch", json=purchases)
    assert response.status_code == 201
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(SalesDailyModel)) == 2
        assert s.scalar(select(func.count()).select_from(PurchaseReceiptModel)) == 2


def test_database_enforces_canonical_batch_and_sales_identities(client, session_factory):
    _ingredient(session_factory, ident="batch-a"); _ingredient(session_factory, ident="batch-b")
    with session_factory() as s:
        s.add_all([
            InventoryLotModel(lot_id="batch-1", store_id="STORE_001", ingredient_id="batch-a", batch_code="CAM-001", received_date=date.today(), initial_quantity=1, unit="kg", source="test", version=1),
            InventoryLotModel(lot_id="batch-2", store_id="STORE_001", ingredient_id="batch-b", batch_code="CAM-001", received_date=date.today(), initial_quantity=1, unit="kg", source="test", version=1),
        ])
        s.commit()
    with session_factory() as s:
        s.add(InventoryLotModel(lot_id="batch-duplicate", store_id="STORE_001", ingredient_id="batch-a", batch_code="CAM-001", received_date=date.today(), initial_quantity=1, unit="kg", source="test", version=1))
        with pytest.raises(IntegrityError): s.commit()
    product = client.post("/api/v1/stores/STORE_001/products", json={"product": "Unique promotion", "sku": "UNIQUE-PROMO", "price": 1, "active": True}).json()
    body = {"source": "pos", "records": [{"external_record_id": "S-1", "date": "2026-07-27", "product_id": product["product_id"], "quantity": "1", "unit_price": 1, "promotion": False}]}
    assert client.post("/api/v1/stores/STORE_001/sales-history/batch", json=body).status_code == 201
    duplicate = client.post("/api/v1/stores/STORE_001/sales-history/batch", json={**body, "records": [{**body["records"][0], "external_record_id": "S-2"}]})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "DUPLICATE_REQUEST"


def test_supplier_settings_and_calendar_production_behavior(client, session_factory):
    _ingredient(session_factory)
    _supplier(session_factory)
    term = {"ingredient_id": "ing-1", "supplier_id": "sup-1", "unit_cost": 100,
            "moq": "1", "pack_size": "1", "lead_time_days": 2, "shelf_life_days": 0, "unit": "kg"}
    created = client.post("/api/v1/stores/STORE_001/supplier-constraints", json=term)
    assert created.status_code == 201
    assert created.json()["shelf_life_days"] == 0
    assert client.get("/api/v1/stores/STORE_001/supplier-constraints").json()["items"][0]["shelf_life_days"] == 0
    invalid = client.post("/api/v1/stores/STORE_001/supplier-constraints", json={**term, "shelf_life_days": -1})
    assert invalid.status_code == 422
    updated = client.put(
        f"/api/v1/stores/STORE_001/supplier-constraints/{created.json()['constraint_id']}",
        json={**term, "unit_cost": 120, "version": 1})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    stale = client.put(
        f"/api/v1/stores/STORE_001/supplier-constraints/{created.json()['constraint_id']}",
        json={**term, "version": 1})
    assert stale.status_code == 409

    settings = client.put("/api/v1/stores/STORE_001/settings", json={
        "monthly_budget": 5_000_000, "forecast_horizon": 7,
        "default_strategy": "balanced", "version": 1})
    assert settings.status_code == 200
    assert settings.json()["remaining_budget"] == 5_000_000
    forbidden = client.put("/api/v1/stores/STORE_001/settings", json={
        "monthly_budget": 1, "reserved_budget": 1, "forecast_horizon": 7,
        "default_strategy": "balanced", "version": settings.json()["version"]})
    assert forbidden.status_code == 422

    payload = {"items": [{"date": "2026-09-02", "holiday": True,
                           "promotion": False, "promotion_note": "discard"}]}
    one = client.put("/api/v1/stores/STORE_001/calendar-features", json=payload)
    two = client.put("/api/v1/stores/STORE_001/calendar-features", json=payload)
    assert one.json()["created_count"] == 1
    assert two.json()["unchanged_count"] == 1
    item = client.get("/api/v1/stores/STORE_001/calendar-features").json()["items"][0]
    assert item["weekend"] is False and item["promotion_note"] is None


def test_purchase_order_budget_and_inventory_state_machine(client, session_factory):
    _ingredient(session_factory)
    _supplier(session_factory)
    client.put("/api/v1/stores/STORE_001/settings", json={
        "monthly_budget": 10000, "forecast_horizon": 7,
        "default_strategy": "balanced", "version": 1})
    now = datetime.now(timezone.utc)
    with session_factory() as s:
        forecast_id, plan_id, rec_id = str(uuid4()), str(uuid4()), str(uuid4())
        s.add(SupplierIngredientTermModel(constraint_id="po-shelf-term", store_id="STORE_001", supplier_id="sup-1", ingredient_id="ing-1", unit_cost=100, moq=Decimal("1"), pack_size=Decimal("1"), lead_time_days=1, shelf_life_days=5, unit="kg", version=1, active=True, source="test"))
        s.add(ForecastRunModel(forecast_run_id=forecast_id, store_id="STORE_001",
            cutoff_date=date.today(), horizon_days=7, quantiles_json="[0.25,0.5,0.75]",
            scope_json="{}", use_latest_calendar=True, status="completed",
            engine_status="test_adapter", request_hash="f"))
        s.add(PlanRunModel(plan_run_id=plan_id, store_id="STORE_001",
            forecast_run_id=forecast_id, strategy="balanced", budget_limit=10000,
            as_of_date=date.today(), include_open_purchase_orders=True,
            status="completed", engine_status="test_adapter", request_hash="p",
            warnings_json="[]"))
        s.add(RecommendationModel(recommendation_id=rec_id, plan_run_id=plan_id,
            store_id="STORE_001", ingredient_id="ing-1", unit="kg",
            order_quantity=Decimal("10"), unit_cost=100, cost=1000,
            supplier_id="sup-1", moq=Decimal("1"), pack_size=Decimal("1"),
            lead_time_days=1))
        s.commit()
    draft = client.post("/api/v1/stores/STORE_001/purchase-orders",
                        json={"plan_run_id": plan_id, "lines": [{"recommendation_id": rec_id}]},
                        headers={"Idempotency-Key": "po-create"}).json()["orders"][0]
    assert draft["status"] == "draft"
    confirmed = client.post(
        f"/api/v1/stores/STORE_001/purchase-orders/{draft['po_id']}/confirm",
        json={"version": 1, "confirmed_at": now.isoformat()})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "ordered"
    assert client.get("/api/v1/stores/STORE_001/settings").json()["reserved_budget"] == 1000
    assert client.get("/api/v1/stores/STORE_001/inventory").json()["total"] == 0
    line = confirmed.json()["lines"][0]
    receive_body = {"version": 2, "received_at": (now + timedelta(hours=1)).isoformat(),
                    "delivery_reference": "DN-1", "lines": [{
                        "po_line_id": line["po_line_id"], "lots": [{
                            "quantity": "10", "expiry_date": (date.today()+timedelta(days=2)).isoformat(),
                            "supplier_lot_code": "LOT-1"}]}]}
    received = client.post(
        f"/api/v1/stores/STORE_001/purchase-orders/{draft['po_id']}/receive",
        json=receive_body, headers={"Idempotency-Key": "po-receive"})
    replay = client.post(
        f"/api/v1/stores/STORE_001/purchase-orders/{draft['po_id']}/receive",
        json=receive_body, headers={"Idempotency-Key": "po-receive"})
    assert received.status_code == replay.status_code == 201
    assert received.json()["status"] == "received"
    settings = client.get("/api/v1/stores/STORE_001/settings").json()
    assert settings["reserved_budget"] == 0 and settings["spent_budget"] == 1000
    assert settings["remaining_budget"] == 9000
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(InventoryLotModel)) == 1
        assert s.scalar(select(func.sum(InventoryMovementModel.quantity_delta))) == Decimal("10")
        assert s.scalar(select(func.count()).select_from(PurchaseOrderModel)) == 1
        po_line = s.get(PurchaseOrderLineModel, line["po_line_id"])
        lot = s.scalar(select(InventoryLotModel))
        assert po_line.shelf_life_days == 5
        assert lot.expiry_date == date.today() + timedelta(days=2)


def test_inventory_count_adjustment_atomic_version_and_replay(client, session_factory):
    _ingredient(session_factory)
    now = datetime.now(timezone.utc)
    with session_factory() as s:
        s.add(InventoryLotModel(lot_id="lot-1", store_id="STORE_001",
            ingredient_id="ing-1", received_date=date.today(),
            initial_quantity=Decimal("5"), unit="kg", source="manual", version=1))
        s.add(InventoryMovementModel(movement_id="move-open", store_id="STORE_001",
            lot_id="lot-1", movement_type="opening_balance",
            quantity_delta=Decimal("5"), unit="kg", occurred_at=now, source="manual"))
        s.commit()
    counted = client.post("/api/v1/stores/STORE_001/inventory-counts",
        json={"counted_at": now.isoformat(), "lines": [{
            "lot_id": "lot-1", "counted_quantity": "4", "unit": "kg"}]},
        headers={"Idempotency-Key": "count-1"})
    assert counted.status_code == 201
    assert counted.json()["adjustments"][0]["quantity_delta"] == "-1.000000"
    adjusted = client.post("/api/v1/stores/STORE_001/inventory-adjustments",
        json={"occurred_at": now.isoformat(), "reference": "WASTE-1", "lines": [{
            "lot_id": "lot-1", "expected_version": 2,
            "quantity_delta": "-1", "unit": "kg", "reason": "waste"}]},
        headers={"Idempotency-Key": "adjust-1"})
    replay = client.post("/api/v1/stores/STORE_001/inventory-adjustments",
        json={"occurred_at": now.isoformat(), "reference": "WASTE-1", "lines": [{
            "lot_id": "lot-1", "expected_version": 2,
            "quantity_delta": "-1", "unit": "kg", "reason": "waste"}]},
        headers={"Idempotency-Key": "adjust-1"})
    assert adjusted.status_code == replay.status_code == 201
    with session_factory() as s:
        assert s.scalar(select(func.sum(InventoryMovementModel.quantity_delta))) == Decimal("3")
        assert s.scalar(select(func.count()).select_from(InventoryMovementModel)) == 3


def test_model_unavailable_remains_explicitly_blocked(client, session_factory):
    _ingredient(session_factory)
    _supplier(session_factory)
    term = client.post("/api/v1/stores/STORE_001/supplier-constraints", json={
        "ingredient_id": "ing-1", "supplier_id": "sup-1", "unit_cost": 100,
        "moq": "1", "pack_size": "1", "lead_time_days": 2, "unit": "kg",
    })
    assert term.status_code == 201
    forecast_body = {"cutoff_date": "2026-07-28", "horizon_days": 7,
                     "quantiles": [0.25, 0.5, 0.75],
                     "scope": {"ingredient_ids": []}, "use_latest_calendar": True}
    created = client.post("/api/v1/stores/STORE_001/forecast-runs",
                          json=forecast_body, headers={"Idempotency-Key": "forecast-fallback"})
    replay = client.post("/api/v1/stores/STORE_001/forecast-runs",
                         json=forecast_body, headers={"Idempotency-Key": "forecast-fallback"})
    assert created.status_code == replay.status_code == 503
    assert created.json()["code"] == replay.json()["code"] == "MODEL_NOT_READY"
    forecast_id = created.json()["details"]["forecast_run_id"]
    assert forecast_id == replay.json()["details"]["forecast_run_id"]
    status = client.get(f"/api/v1/stores/STORE_001/forecast-runs/{forecast_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "blocked"
    assert status.json()["engine_status"] == "model_unavailable"
    forecast_result = client.get(
        f"/api/v1/stores/STORE_001/forecast-runs/{forecast_id}/result")
    assert forecast_result.status_code == 503
    assert forecast_result.json()["code"] == "MODEL_NOT_READY"
    plan = client.post("/api/v1/stores/STORE_001/plan-runs", json={
        "forecast_run_id": forecast_id, "strategy": "balanced",
        "budget_limit": 1000, "as_of_date": "2026-07-28",
        "include_open_purchase_orders": True,
    }, headers={"Idempotency-Key": "plan-fallback"})
    plan_replay = client.post("/api/v1/stores/STORE_001/plan-runs", json={
        "forecast_run_id": forecast_id, "strategy": "balanced",
        "budget_limit": 1000, "as_of_date": "2026-07-28",
        "include_open_purchase_orders": True,
    }, headers={"Idempotency-Key": "plan-fallback"})
    assert plan.status_code == plan_replay.status_code == 409
    assert plan.json()["code"] == plan_replay.json()["code"] == "FORECAST_RUN_NOT_COMPLETED"
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ForecastRunModel)) == 1
        assert s.scalar(select(func.count()).select_from(PlanRunModel)) == 0
        assert s.scalar(select(func.count()).select_from(RecommendationModel)) == 0
        assert s.scalar(select(func.count()).select_from(PurchaseOrderModel)) == 0


def test_forecast_fallback_preserves_real_validation_errors(client):
    body = {"cutoff_date": "2026-07-28", "horizon_days": 7,
            "quantiles": [0.25, 0.5, 0.75],
            "scope": {"ingredient_ids": []}, "use_latest_calendar": True}
    assert client.post("/api/v1/stores/STORE_MISSING/forecast-runs", json=body).status_code == 404
    invalid = client.post("/api/v1/stores/STORE_001/forecast-runs",
                          json={**body, "horizon_days": 0})
    assert invalid.status_code == 422


def test_bootstrap_and_dashboard_use_persisted_database(client):
    product = client.post("/api/v1/stores/STORE_001/products", json={
        "product": "Persisted", "sku": "PERSISTED", "price": None, "active": True
    }).json()
    client.put("/api/v1/stores/STORE_001/settings", json={
        "monthly_budget": 1234, "forecast_horizon": 9,
        "default_strategy": "safe", "version": 1})
    bootstrap = client.get("/api/v1/stores/STORE_001/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["products"][0]["product_id"] == product["product_id"]
    assert bootstrap.json()["settings"]["monthly_budget"] == 1234
    dashboard = client.get("/api/v1/stores/STORE_001/dashboard").json()
    assert dashboard["active_product_count"] == 1
    assert dashboard["remaining_budget"] == 1234
