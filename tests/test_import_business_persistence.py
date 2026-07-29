import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select

from app.core.provenance import purchase_business_key, source_row_hash
from app.models.business import (
    CalendarFeatureModel, IngredientModel, InventoryLotModel, InventoryMovementModel, ProductModel,
    PurchaseReceiptModel, RecipeVersionModel, SalesDailyModel, UsageDailyModel,
    StoreSettingsModel, SupplierIngredientTermModel,
)
from app.models.import_normalized import ImportIssueModel, ImportJobModel
from app.services.business_persistence import ImportBusinessPersistenceService


def upload_confirm_process(client, csv: bytes, mapping: dict[str, str], sheet_type: str, *, name="data.csv"):
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
    processed = client.post(f"/api/v1/imports/{body['import_id']}/process")
    return body, processed


def test_provenance_hash_is_stable_and_row_sensitive():
    args = dict(store_id="STORE_001", import_id="i", profile_id="p", sheet_id="s", sheet_type="usage_history")
    first = source_row_hash(**args, source_row=2, row={"quantity": Decimal("1.200"), "date": date(2026, 1, 1)})
    reordered = source_row_hash(**args, source_row=2, row={"date": date(2026, 1, 1), "quantity": Decimal("1.2")})
    assert first == reordered
    assert first != source_row_hash(**args, source_row=3, row={"quantity": Decimal("1.2"), "date": date(2026, 1, 1)})
    assert purchase_business_key(store_id="S", quantity=Decimal("1.0")) == purchase_business_key(quantity=Decimal("1"), store_id="S")


def test_sales_aggregate_correction_and_completed_replay(client):
    csv = b"day,product,qty,price\n2026-01-01,Coffee,2,10000\n2026-01-01,Coffee,3,10000\n"
    mapping = {"day": "date", "product": "product_name", "qty": "quantity_sold", "price": "selling_price"}
    body, processed = upload_confirm_process(client, csv, mapping, "sales_history")
    assert processed.status_code == 200, processed.text
    with client.app.state.session_factory() as session:
        sale = session.scalar(select(SalesDailyModel))
        assert sale.quantity == Decimal("5.000000")
        assert session.scalar(select(func.count()).select_from(ProductModel)) == 1
        job = session.get(ImportJobModel, body["import_id"])
        assert job.business_persisted_at is not None
        assert job.business_schema_version == "20260728_0004"
        assert json.loads(job.business_write_summary_json)["sales_records_created"] == 1
    replay = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert replay.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SalesDailyModel)) == 1

    corrected = b"day,product,qty,price\n2026-01-01,Coffee,7,10000\n"
    _, response = upload_confirm_process(client, corrected, mapping, "sales_history", name="corrected.csv")
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(SalesDailyModel.quantity)) == Decimal("7.000000")


def test_usage_conversion_aggregate_without_inventory_movement(client):
    csv = b"day,ingredient,qty,unit\n2026-01-01,Flour,1,kg\n2026-01-01,Flour,500,g\n"
    mapping = {"day": "date", "ingredient": "ingredient_name", "qty": "quantity_used", "unit": "unit"}
    _, response = upload_confirm_process(client, csv, mapping, "usage_history")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        usage = session.scalar(select(UsageDailyModel))
        assert usage.quantity == Decimal("1.500000")
        assert usage.unit == "kg"
        assert session.scalar(select(func.count()).select_from(InventoryMovementModel)) == 0


def test_inventory_snapshot_reconciliation(client):
    mapping = {"day": "snapshot_date", "ingredient": "ingredient_name", "qty": "on_hand", "unit": "unit", "batch": "batch_id"}
    first = b"day,ingredient,qty,unit,batch\n2026-01-01,Milk,10,l,B1\n"
    _, response = upload_confirm_process(client, first, mapping, "inventory")
    assert response.status_code == 200, response.text
    same = b"day,ingredient,qty,unit,batch\n2026-01-02,Milk,10,l,B1\n"
    _, response = upload_confirm_process(client, same, mapping, "inventory", name="same.csv")
    assert response.status_code == 200
    increased = b"day,ingredient,qty,unit,batch\n2026-01-03,Milk,12,l,B1\n"
    _, response = upload_confirm_process(client, increased, mapping, "inventory", name="more.csv")
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InventoryLotModel)) == 1
        movements = list(session.scalars(select(InventoryMovementModel).order_by(InventoryMovementModel.created_at)))
        assert [item.movement_type for item in movements] == ["opening_balance", "physical_count_adjustment"]
        assert sum(item.quantity_delta for item in movements) == Decimal("12.000000")


def test_purchase_dedup_does_not_change_inventory(client):
    csv = b"day,ingredient,qty,unit,cost,supplier,batch\n2026-01-01,Sugar,5,kg,20000,ABC,B1\n"
    mapping = {"day": "purchase_date", "ingredient": "ingredient_name", "qty": "quantity_received", "unit": "unit", "cost": "unit_price", "supplier": "supplier_name", "batch": "batch_id"}
    _, first = upload_confirm_process(client, csv, mapping, "purchase_history")
    _, second = upload_confirm_process(client, csv, mapping, "purchase_history", name="duplicate.csv")
    assert first.status_code == second.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PurchaseReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(InventoryLotModel)) == 0
        assert session.scalar(select(func.count()).select_from(InventoryMovementModel)) == 0


def test_recipe_persistence_and_all_or_nothing_failure(client):
    mapping = {"product": "product_name", "ingredient": "ingredient_name", "qty": "ingredient_quantity", "unit": "ingredient_unit", "effective": "effective_date"}
    valid = b"product,ingredient,qty,unit,effective\nCake,Flour,1,kg,2026-01-01\nCake,Sugar,200,g,2026-01-01\n"
    _, response = upload_confirm_process(client, valid, mapping, "recipes")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecipeVersionModel)) == 1

    invalid = b"product,ingredient,qty,unit,effective\nBad,Water,1,l,2026-01-01\nBad,Water,1,kg,2026-01-01\n"
    body, response = upload_confirm_process(client, invalid, mapping, "recipes", name="invalid.csv")
    assert response.status_code == 422
    with client.app.state.session_factory() as session:
        assert session.scalar(select(ProductModel).where(ProductModel.normalized_name == "bad")) is None
        job = session.get(ImportJobModel, body["import_id"])
        assert job.status == "failed"
        assert session.scalar(select(func.count()).select_from(ImportIssueModel).where(ImportIssueModel.import_id == body["import_id"], ImportIssueModel.issue_source == "business_persistence")) == 1


def test_supplier_term_versioning_and_conversion(client):
    mapping = {"supplier": "supplier_name", "ingredient": "ingredient_name", "moq": "minimum_order_quantity", "unit": "order_unit", "pack": "package_size", "lead": "lead_time_days", "cost": "unit_price"}
    first = b"supplier,ingredient,moq,unit,pack,lead,cost\nABC,Flour,1000,g,500,2,20000\n"
    _, response = upload_confirm_process(client, first, mapping, "supplier_constraints")
    assert response.status_code == 200, response.text
    _, response = upload_confirm_process(client, first, mapping, "supplier_constraints", name="same-term.csv")
    assert response.status_code == 200
    changed = b"supplier,ingredient,moq,unit,pack,lead,cost\nABC,Flour,2,kg,1,3,21000\n"
    _, response = upload_confirm_process(client, changed, mapping, "supplier_constraints", name="changed-term.csv")
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        terms = list(session.scalars(select(SupplierIngredientTermModel).order_by(SupplierIngredientTermModel.version)))
        assert len(terms) == 2
        assert terms[0].moq == Decimal("1000.000000")
        assert terms[1].moq == Decimal("2000.000000")
        assert not terms[0].active and terms[1].active


def test_supplier_term_missing_unit_is_warning_not_500(client):
    existing = client.post("/api/v1/stores/STORE_001/ingredients", json={
        "ingredient": "Flour", "sku": "FLOUR", "base_unit": "kg", "active": True,
    })
    assert existing.status_code == 201
    mapping = {
        "supplier": "supplier_name", "ingredient": "ingredient_name",
        "moq": "minimum_order_quantity", "unit": "order_unit",
        "pack": "package_size", "lead": "lead_time_days", "cost": "unit_price",
    }
    csv = b"supplier,ingredient,moq,unit,pack,lead,cost\nABC,Flour,2,,1,3,21000\n"
    body, response = upload_confirm_process(client, csv, mapping, "supplier_constraints")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SupplierIngredientTermModel)) == 0
        issue = session.scalar(select(ImportIssueModel).where(
            ImportIssueModel.import_id == body["import_id"],
            ImportIssueModel.code == "UNIT_MISSING",
        ))
        assert issue is not None
        assert json.loads(issue.details_json)["fallback_unit"] == "None"


def test_supplier_persistence_handles_missing_ingredient_base_unit(session_factory):
    with session_factory() as session:
        service = ImportBusinessPersistenceService(session)
        service._ingredient = lambda *_args, **_kwargs: SimpleNamespace(
            ingredient_id="ing-missing-unit", base_unit=None)
        service._supplier = lambda *_args, **_kwargs: SimpleNamespace(
            supplier_id="sup-missing-unit")
        job = SimpleNamespace(store_id="STORE_001", import_id="import-missing-unit")
        sheet = {"profile_id": "profile-missing-unit", "rows": [{
            "supplier_name": "Supplier", "ingredient_name": "Ingredient",
            "minimum_order_quantity": "2", "order_unit": "kg",
            "package_size": "1", "_source_excel_row": 2,
        }]}
        service._persist_supplier_constraints(job, sheet)
        assert service.summary.warnings == 1
        assert service.summary.rows_skipped == 1
        issue = next(item for item in session.new if isinstance(item, ImportIssueModel))
        assert json.loads(issue.details_json)["target_unit"] == "None"


def test_calendar_and_settings_upsert(client):
    calendar_mapping = {"day": "date", "weekend": "is_weekend", "holiday": "is_holiday", "closed": "is_store_closed", "promotion": "is_promotion", "name": "promotion_name"}
    first = b"day,weekend,holiday,closed,promotion,name\n2026-01-03,false,false,false,false,\n"
    _, response = upload_confirm_process(client, first, calendar_mapping, "calendar_features")
    assert response.status_code == 200
    changed = b"day,weekend,holiday,closed,promotion,name\n2026-01-03,false,true,false,true,Tet\n"
    _, response = upload_confirm_process(client, changed, calendar_mapping, "calendar_features", name="calendar-update.csv")
    assert response.status_code == 200
    settings_mapping = {"kind": "constraint_type", "value": "value"}
    _, response = upload_confirm_process(client, b"kind,value\nmonthly_budget,1000000\nforecast_horizon,14\n", settings_mapping, "business_constraints")
    assert response.status_code == 200
    _, response = upload_confirm_process(client, b"kind,value\nmonthly_budget,2000000\nforecast_horizon,21\n", settings_mapping, "business_constraints", name="settings-update.csv")
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        calendar = session.scalar(select(CalendarFeatureModel))
        assert calendar.is_weekend is True
        assert calendar.is_holiday is True
        settings = session.scalar(select(StoreSettingsModel))
        assert settings.monthly_budget == 2_000_000
        assert settings.forecast_horizon == 21
        assert settings.version == 2
