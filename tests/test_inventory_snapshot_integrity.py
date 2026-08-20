from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models.business import InventoryLotModel, InventoryMovementModel, SupplierIngredientTermModel
from shelfcash_core.inventory.contracts import InventoryLot
from shelfcash_core.inventory.fefo import fefo_sort_key


def _import(client, csv: bytes, mapping: dict[str, str], sheet_type: str, name: str):
    created = client.post(
        "/api/v1/imports", data={"store_id": "STORE_001", "forecast_date": "2026-01-01"},
        files={"files": (name, csv, "text/csv")},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    confirmed = client.post(
        f"/api/v1/imports/{body['import_id']}/confirm",
        json={"mappings": [{"sheet_id": body["sheets"][0]["sheet_id"], "sheet_type": sheet_type, "column_mapping": mapping}]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return client.post(f"/api/v1/imports/{body['import_id']}/process")


INVENTORY_MAPPING = {
    "snapshot": "snapshot_date", "ingredient": "ingredient_name", "on_hand": "on_hand",
    "unit": "unit", "batch": "batch_id", "received": "received_date",
    "expiry": "expiry_date", "supplier": "supplier_name", "warehouse": "warehouse_name",
}


def test_inventory_snapshot_separates_observation_and_receipt_time(client, session_factory):
    first = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-10,Milk,20,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    assert _import(client, first, INVENTORY_MAPPING, "inventory", "first.csv").status_code == 200
    same_quantity = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-12,Milk,20,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    assert _import(client, same_quantity, INVENTORY_MAPPING, "inventory", "same.csv").status_code == 200
    second = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-14,Milk,16,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    assert _import(client, second, INVENTORY_MAPPING, "inventory", "second.csv").status_code == 200
    unknown = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-10,Flour,4,kg,B2,,2026-02-20,,\n"
    assert _import(client, unknown, INVENTORY_MAPPING, "inventory", "unknown.csv").status_code == 200

    with session_factory() as session:
        milk = session.scalar(select(InventoryLotModel).where(InventoryLotModel.batch_code == "B1"))
        flour = session.scalar(select(InventoryLotModel).where(InventoryLotModel.batch_code == "B2"))
        movements = list(session.scalars(select(InventoryMovementModel).where(InventoryMovementModel.lot_id == milk.lot_id).order_by(InventoryMovementModel.occurred_at)))
        assert milk.received_date == date(2026, 1, 5)
        assert milk.received_date_status == "declared"
        assert flour.received_date is None and flour.received_date_status == "unknown"
        assert [item.movement_type for item in movements] == ["opening_balance", "physical_count_adjustment"]
        assert [item.quantity_delta for item in movements] == [Decimal("20.000000"), Decimal("-4.000000")]
        assert [item.occurred_at.date() for item in movements] == [date(2026, 1, 9), date(2026, 1, 13)]
        assert all(item.source == "inventory_snapshot" and "snapshot_date=" in (item.note or "") for item in movements)


def test_inventory_snapshot_rejects_identity_conflicts_and_stale_rows(client, session_factory):
    first = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-10,Milk,20,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    assert _import(client, first, INVENTORY_MAPPING, "inventory", "first.csv").status_code == 200
    with session_factory() as session:
        before = session.scalar(select(func.count()).select_from(InventoryMovementModel))

    expiry_conflict = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-12,Milk,10,kg,B1,2026-01-05,2026-01-21,Supplier A,Cold A\n"
    response = _import(client, expiry_conflict, INVENTORY_MAPPING, "inventory", "expiry-conflict.csv")
    assert response.status_code == 409 and response.json()["code"] == "INVENTORY_LOT_METADATA_CONFLICT"
    unit_conflict = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-12,Milk,1000,g,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    response = _import(client, unit_conflict, INVENTORY_MAPPING, "inventory", "unit-conflict.csv")
    assert response.status_code == 409 and response.json()["code"] == "INVENTORY_LOT_METADATA_CONFLICT"
    warehouse_conflict = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-12,Milk,10,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold B\n"
    response = _import(client, warehouse_conflict, INVENTORY_MAPPING, "inventory", "warehouse-conflict.csv")
    assert response.status_code == 409 and response.json()["code"] == "INVENTORY_LOT_METADATA_CONFLICT"
    stale = b"snapshot,ingredient,on_hand,unit,batch,received,expiry,supplier,warehouse\n2026-01-09,Milk,10,kg,B1,2026-01-05,2026-01-20,Supplier A,Cold A\n"
    response = _import(client, stale, INVENTORY_MAPPING, "inventory", "stale.csv")
    assert response.status_code == 422 and response.json()["code"] == "INVENTORY_SNAPSHOT_OUT_OF_ORDER"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InventoryMovementModel)) == before


def test_inventory_snapshot_requires_batch_and_rejects_duplicates(client):
    missing_batch = b"snapshot,ingredient,on_hand,unit,batch\n2026-01-10,Milk,20,kg,\n"
    response = _import(client, missing_batch, {key: value for key, value in INVENTORY_MAPPING.items() if key in {"snapshot", "ingredient", "on_hand", "unit", "batch"}}, "inventory", "no-batch.csv")
    assert response.status_code == 422
    assert response.json()["details"]["issues"][0]["code"] == "INVENTORY_BATCH_ID_REQUIRED"
    duplicate = b"snapshot,ingredient,on_hand,unit,batch\n2026-01-10,Milk,20,kg,B1\n2026-01-10,Milk,10,kg,B1\n"
    response = _import(client, duplicate, {key: value for key, value in INVENTORY_MAPPING.items() if key in {"snapshot", "ingredient", "on_hand", "unit", "batch"}}, "inventory", "duplicate.csv")
    assert response.status_code == 422 and response.json()["code"] == "INVENTORY_SNAPSHOT_DUPLICATE_BATCH"


def test_fefo_unknown_received_date_is_stable_without_synthetic_age():
    known = InventoryLot(lot_id="known", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", received_date=date(2026, 1, 2), expiry_date=date(2026, 1, 10))
    unknown_a = InventoryLot(lot_id="unknown-a", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", received_date=None, expiry_date=date(2026, 1, 10))
    unknown_b = InventoryLot(lot_id="unknown-b", store_id="s", ingredient_id="i", quantity_remaining=1, unit="kg", received_date=None, expiry_date=date(2026, 1, 10))
    assert [lot.lot_id for lot in sorted([unknown_b, known, unknown_a], key=fefo_sort_key)] == ["known", "unknown-a", "unknown-b"]


def test_supplier_terms_require_declared_price_and_lead_time(client, session_factory):
    mapping = {"supplier": "supplier_name", "ingredient": "ingredient_name", "moq": "minimum_order_quantity", "order": "order_unit", "pack": "package_size", "unit": "package_base_unit", "lead": "lead_time_days", "price": "unit_price"}
    missing_price = b"supplier,ingredient,moq,order,pack,unit,lead,price\nA,Milk,1,box,1,kg,0,\n"
    response = _import(client, missing_price, mapping, "supplier_constraints", "missing-price.csv")
    assert response.status_code == 422
    assert response.json()["details"]["issues"][0]["code"] == "UNIT_PRICE_NOT_CONFIGURED"
    missing_lead = b"supplier,ingredient,moq,order,pack,unit,lead,price\nA,Milk,1,box,1,kg,,0\n"
    response = _import(client, missing_lead, mapping, "supplier_constraints", "missing-lead.csv")
    assert response.status_code == 422
    assert response.json()["details"]["issues"][0]["code"] == "LEAD_TIME_NOT_CONFIGURED"
    declared_zero = b"supplier,ingredient,moq,order,pack,unit,lead,price\nA,Milk,1,box,1,kg,0,0\n"
    assert _import(client, declared_zero, mapping, "supplier_constraints", "declared-zero.csv").status_code == 200
    with session_factory() as session:
        term = session.scalar(select(SupplierIngredientTermModel))
        assert term.unit_cost == 0 and term.lead_time_days == 0
        assert term.unit_price_status == term.lead_time_status == "declared"
