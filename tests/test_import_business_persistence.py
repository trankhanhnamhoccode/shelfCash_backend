import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core.provenance import purchase_business_key, source_row_hash
from app.core.packaging_units import normalize_packaging_unit
from app.core.rule_mapper import map_sheet_rules
from app.core.units import normalize_unit, validate_compatible
from app.core.exceptions import ValidationError
from app.schemas.llm import SheetProfile
from app.models.business import (
    CalendarFeatureModel, IngredientModel, InventoryConstraintModel, InventoryLotModel, InventoryMovementModel, ProductModel, ProductBundleLineModel,
    PurchaseReceiptModel, RecipeLineModel, RecipeVersionModel, SalesDailyModel, UsageDailyModel,
    StoreSettingsModel, SupplierIngredientTermModel,
)
from app.models.import_normalized import ImportIssueModel, ImportJobModel
from app.models.audit_log import AuditLogModel
from app.services.business_persistence import ImportBusinessPersistenceService
from app.services.business_persistence import normalize_delivery_days


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


MENU_MAPPING = {
    "sku": "product_sku", "type": "item_type", "name": "product_name",
    "components": "combo_components", "unit": "selling_unit",
    "price": "selling_price", "status": "status",
}


def test_combo_name_resolver_unique_and_row_order_independent(client):
    for rows in (
        ["TEA-500,single,Milk Tea 500ml,,ly,30000,active", "COMBO-1,combo,Afternoon Combo,1 x Milk Tea,combo,40000,active"],
        ["COMBO-2,combo,Evening Combo,1 x Milk Tea,combo,40000,active", "TEA-700,single,Other Tea 700ml,,ly,30000,active"],
    ):
        csv = ("sku,type,name,components,unit,price,status\n" + "\n".join(rows) + "\n").encode()
        _, response = upload_confirm_process(client, csv, MENU_MAPPING, "menu", name=f"{rows[0][:5]}.csv")
        assert response.status_code == 200, response.text
    bootstrap = client.get("/api/v1/stores/STORE_001/bootstrap").json()
    combo = next(item for item in bootstrap["menu"] if item["sku"] == "COMBO-1")
    assert combo["components"][0]["component_sku"] == "TEA-500"
    assert combo["components"][0]["component_product"] == "Milk Tea 500ml"


def test_combo_ambiguity_rolls_back_and_sku_resolves_exact_variant(client):
    variants = (
        "sku,type,name,components,unit,price,status\n"
        "TEA-500,single,Milk Tea 500ml,,ly,30000,active\n"
        "TEA-700,single,Milk Tea 700ml,,ly,35000,active\n"
    ).encode()
    assert upload_confirm_process(client, variants, MENU_MAPPING, "menu", name="variants.csv")[1].status_code == 200
    with client.app.state.session_factory() as session:
        before = (session.scalar(select(func.count()).select_from(ProductModel)), session.scalar(select(func.count()).select_from(ProductBundleLineModel)))
    ambiguous = b"sku,type,name,components,unit,price,status\nBAD-C,combo,Bad Combo,1 x Milk Tea,combo,40000,active\n"
    _, response = upload_confirm_process(client, ambiguous, MENU_MAPPING, "menu", name="ambiguous.csv")
    assert response.status_code == 422
    assert response.json()["code"] == "AMBIGUOUS_PRODUCT_VARIANT"
    assert response.json()["details"]["candidate_count"] == 2
    with client.app.state.session_factory() as session:
        after = (session.scalar(select(func.count()).select_from(ProductModel)), session.scalar(select(func.count()).select_from(ProductBundleLineModel)))
    assert after == before

    explicit_mapping = {**MENU_MAPPING, "component_sku": "component_sku", "qty": "component_quantity"}
    explicit = b"sku,type,name,components,unit,price,status,component_sku,qty\nGOOD-C,combo,Good Combo,,combo,40000,active,TEA-700,1\n"
    _, response = upload_confirm_process(client, explicit, explicit_mapping, "menu", name="explicit.csv")
    assert response.status_code == 200, response.text
    assert response.json()["status"] in {"completed", "processed"}
    combo = next(item for item in client.get("/api/v1/stores/STORE_001/bootstrap").json()["menu"] if item["sku"] == "GOOD-C")
    assert combo["components"][0]["component_sku"] == "TEA-700"


def test_component_product_id_store_scope_and_inactive_sku(client, session_factory):
    with session_factory() as session:
        active = ProductModel(product_id="component-active", store_id="STORE_001", product="Exact", normalized_name="exact", sku="EXACT-1", price=100, item_type="single", selling_unit="ly", active=True, source="test", version=1)
        inactive = ProductModel(product_id="component-inactive", store_id="STORE_001", product="Inactive", normalized_name="inactive", sku="OFF-1", price=100, item_type="single", selling_unit="ly", active=False, source="test", version=1)
        session.add_all([active, inactive]); session.commit()
    mapping = {**MENU_MAPPING, "component_id": "component_product_id", "component_sku": "component_sku"}
    valid = b"sku,type,name,components,unit,price,status,component_id,component_sku\nID-C,combo,ID Combo,,combo,90,active,component-active,\n"
    assert upload_confirm_process(client, valid, mapping, "menu", name="by-id.csv")[1].status_code == 200
    invalid = b"sku,type,name,components,unit,price,status,component_id,component_sku\nOFF-C,combo,Off Combo,,combo,90,active,,OFF-1\n"
    response = upload_confirm_process(client, invalid, mapping, "menu", name="inactive.csv")[1]
    assert response.status_code == 422 and response.json()["code"] == "COMPONENT_NOT_FOUND"


def test_legacy_null_sku_is_only_upgraded_for_unambiguous_batch_identity(client, session_factory):
    with session_factory() as session:
        session.add(ProductModel(product_id="legacy-one", store_id="STORE_001", product="Legacy Tea", normalized_name="legacy tea", sku=None, price=100, item_type="single", selling_unit="ly", active=True, source="legacy", version=1))
        session.commit()
    single = b"sku,type,name,components,unit,price,status\nLEG-500,single,Legacy Tea,,ly,120,active\n"
    assert upload_confirm_process(client, single, MENU_MAPPING, "menu", name="legacy-upgrade.csv")[1].status_code == 200
    with session_factory() as session:
        products = list(session.scalars(select(ProductModel).where(ProductModel.product == "Legacy Tea")))
        assert len(products) == 1 and products[0].product_id == "legacy-one" and products[0].sku == "LEG-500"
        assert session.scalar(select(func.count()).select_from(AuditLogModel).where(AuditLogModel.action == "legacy_product_sku_upgraded")) == 1

    with session_factory() as session:
        session.add(ProductModel(product_id="legacy-many", store_id="STORE_001", product="Variant Tea", normalized_name="variant tea", sku=None, price=100, item_type="single", selling_unit="ly", active=True, source="legacy", version=1))
        session.commit()
    variants = b"sku,type,name,components,unit,price,status\nVAR-500,single,Variant Tea,,ly,120,active\nVAR-700,single,Variant Tea,,ly,140,active\n"
    assert upload_confirm_process(client, variants, MENU_MAPPING, "menu", name="legacy-ambiguous.csv")[1].status_code == 200
    with session_factory() as session:
        products = list(session.scalars(select(ProductModel).where(ProductModel.product == "Variant Tea")))
        assert len(products) == 3
        assert next(p for p in products if p.product_id == "legacy-many").sku is None


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
        assert job.business_schema_version == "20260804_0018"
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
        assert session.scalar(select(InventoryLotModel.snapshot_date)) == date(2026, 1, 3)
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


def test_purchase_history_received_date_is_persisted_as_receipt_date(client):
    csv = b"received,ingredient,qty,unit,supplier,batch\n2026-01-03,Sugar,5,kg,ABC,B1\n"
    mapping = {
        "received": "received_date", "ingredient": "ingredient_name",
        "qty": "quantity_received", "unit": "unit",
        "supplier": "supplier_name", "batch": "batch_id",
    }

    _, response = upload_confirm_process(client, csv, mapping, "purchase_history")

    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        receipt = session.scalar(select(PurchaseReceiptModel))
        assert receipt.receipt_date.isoformat() == "2026-01-03"
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


def test_same_name_sku_variants_keep_distinct_recipes_end_to_end(client):
    menu_mapping = {
        "sku": "product_sku", "type": "item_type", "name": "product_name",
        "unit": "selling_unit", "price": "selling_price", "status": "status",
    }
    menu = (
        "sku,type,name,unit,price,status\n"
        "STC-350,Món lẻ,Sinh tố chuối,ly,35000,Đang bán\n"
        "STC-500,Món lẻ,Sinh tố chuối,ly,45000,Đang bán\n"
    ).encode("utf-8")
    menu_import, response = upload_confirm_process(client, menu, menu_mapping, "menu", name="menu.csv")
    assert response.status_code == 200, response.text

    recipe_mapping = {
        "sku": "product_sku", "product": "product_name", "ingredient": "ingredient_name",
        "qty": "ingredient_quantity", "unit": "ingredient_unit", "effective": "effective_date",
    }
    recipes = (
        "sku,product,ingredient,qty,unit,effective\n"
        "STC-350,Sinh tố chuối,Chuối,0.10,kg,2026-06-01\n"
        "STC-350,Sinh tố chuối,Sữa tươi,0.12,l,2026-06-01\n"
        "STC-500,Sinh tố chuối,Chuối,0.14,kg,2026-06-01\n"
        "STC-500,Sinh tố chuối,Sữa tươi,0.17,l,2026-06-01\n"
    ).encode("utf-8")
    recipe_import, response = upload_confirm_process(client, recipes, recipe_mapping, "recipes", name="recipes.csv")
    assert response.status_code == 200, response.text

    bootstrap = client.get("/api/v1/stores/STORE_001/bootstrap")
    assert bootstrap.status_code == 200
    body = bootstrap.json()
    variants = {item["sku"]: item for item in body["products"]}
    assert {"STC-350", "STC-500"} <= variants.keys()
    assert variants["STC-350"]["product"] == variants["STC-500"]["product"] == "Sinh tố chuối"
    recipe_by_sku = {item["sku"]: item for item in body["recipes"]}
    quantities = {
        sku: {line["ingredient"]: line["quantity"] for line in recipe_by_sku[sku]["components"]}
        for sku in ("STC-350", "STC-500")
    }
    assert quantities["STC-350"] == {"Chuối": "0.1", "Sữa tươi": "0.12"}
    assert quantities["STC-500"] == {"Chuối": "0.14", "Sữa tươi": "0.17"}
    assert all(item["components"] == [] for item in body["menu"])

    assert client.post(f"/api/v1/imports/{menu_import['import_id']}/process").status_code == 200
    assert client.post(f"/api/v1/imports/{recipe_import['import_id']}/process").status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductModel).where(ProductModel.sku.in_(["STC-350", "STC-500"]))) == 2
        assert session.scalar(select(func.count()).select_from(RecipeVersionModel)) == 2
        assert session.scalar(select(func.count()).select_from(RecipeLineModel)) == 4


def test_menu_same_sku_conflicting_product_is_rejected(client):
    mapping = {
        "sku": "product_sku", "type": "item_type", "name": "product_name",
        "unit": "selling_unit", "price": "selling_price", "status": "status",
    }
    csv = (
        "sku,type,name,unit,price,status\n"
        "STC-350,Món lẻ,Sinh tố chuối,ly,35000,Đang bán\n"
        "STC-350,Món lẻ,Sinh tố xoài,ly,35000,Đang bán\n"
    ).encode("utf-8")
    _, response = upload_confirm_process(client, csv, mapping, "menu", name="sku-conflict.csv")
    assert response.status_code == 409
    assert response.json()["code"] == "SKU_CONFLICT"


def test_recipe_without_sku_rejects_ambiguous_product_name(client):
    for sku in ("STC-350", "STC-500"):
        created = client.post("/api/v1/stores/STORE_001/products", json={
            "product": "Sinh tố chuối", "sku": sku, "price": 35000, "active": True,
        })
        assert created.status_code == 201
    mapping = {
        "product": "product_name", "ingredient": "ingredient_name",
        "qty": "ingredient_quantity", "unit": "ingredient_unit", "effective": "effective_date",
    }
    csv = "product,ingredient,qty,unit,effective\nSinh tố chuối,Chuối,0.1,kg,2026-06-01\n".encode("utf-8")
    _, response = upload_confirm_process(client, csv, mapping, "recipes", name="ambiguous-recipe.csv")
    assert response.status_code == 422
    assert response.json()["code"] == "MISSING_SKU_FOR_DUPLICATE_NAME"


def test_supplier_term_versioning_and_conversion(client):
    mapping = {"supplier": "supplier_name", "ingredient": "ingredient_name", "moq": "minimum_order_quantity", "unit": "order_unit", "pack": "package_size", "base": "package_base_unit", "lead": "lead_time_days", "cost": "unit_price", "days": "available_delivery_days"}
    first = b"supplier,ingredient,moq,unit,pack,base,lead,cost,days\nABC,Flour,1000,pack,500,g,2,20000,Monday;Wednesday\n"
    _, response = upload_confirm_process(client, first, mapping, "supplier_constraints")
    assert response.status_code == 200, response.text
    _, response = upload_confirm_process(client, first, mapping, "supplier_constraints", name="same-term.csv")
    assert response.status_code == 200
    changed = b"supplier,ingredient,moq,unit,pack,base,lead,cost,days\nABC,Flour,2,bag,1,kg,3,21000,Friday\n"
    _, response = upload_confirm_process(client, changed, mapping, "supplier_constraints", name="changed-term.csv")
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        terms = list(session.scalars(select(SupplierIngredientTermModel).order_by(SupplierIngredientTermModel.version)))
        assert len(terms) == 2
        assert terms[0].moq == Decimal("500000.000000")
        assert terms[1].moq == Decimal("2000.000000")
        assert terms[0].order_unit == "pack"
        assert terms[1].order_unit == "bag"
        assert json.loads(terms[0].available_delivery_days) == [0, 2]
        assert json.loads(terms[1].available_delivery_days) == [4]
        assert not terms[0].active and terms[1].active


def test_supplier_term_unknown_packaging_is_preserved_with_warning(client):
    existing = client.post("/api/v1/stores/STORE_001/ingredients", json={
        "ingredient": "Flour", "sku": "FLOUR", "base_unit": "kg", "active": True,
    })
    assert existing.status_code == 201
    mapping = {
        "supplier": "supplier_name", "ingredient": "ingredient_name",
        "moq": "minimum_order_quantity", "unit": "order_unit",
        "pack": "package_size", "base": "package_base_unit",
        "lead": "lead_time_days", "cost": "unit_price",
    }
    csv = b"supplier,ingredient,moq,unit,pack,base,lead,cost\nABC,Flour,2,pallet,1,kg,3,21000\n"
    body, response = upload_confirm_process(client, csv, mapping, "supplier_constraints")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        term = session.scalar(select(SupplierIngredientTermModel))
        assert term.order_unit == "pallet"
        issue = session.scalar(select(ImportIssueModel).where(
            ImportIssueModel.import_id == body["import_id"],
            ImportIssueModel.code == "UNKNOWN_PACKAGING_UNIT",
        ))
        assert issue is not None
        assert json.loads(issue.details_json)["normalized_value"] == "pallet"


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
            "minimum_order_quantity": "2", "order_unit": "case",
            "package_size": "1", "package_base_unit": "kg",
            "_source_excel_row": 2,
        }]}
        with pytest.raises(ValidationError) as caught:
            service._persist_supplier_constraints(job, sheet)
        assert caught.value.details["field"] == "package_base_unit"


def test_row_issue_policies_atomic_partial_and_preview(client):
    mapping = {"day": "date", "ingredient": "ingredient_name", "qty": "quantity_used", "unit": "unit"}
    invalid = b"day,ingredient,qty,unit\n2026-01-01,Milk,,l\n2026-01-02,,2,l\n"
    body, response = upload_confirm_process(client, invalid, mapping, "usage_history", name="invalid-rows.csv")
    assert response.status_code == 422
    issues = response.json()["details"]["issues"]
    assert {item["field"] for item in issues} == {"quantity_used", "ingredient_name"}
    assert all({"sheet", "row_number", "code", "raw_value", "remediation"} <= item.keys() for item in issues)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(UsageDailyModel)) == 0
        assert session.scalar(select(func.count()).select_from(ImportIssueModel).where(
            ImportIssueModel.import_id == body["import_id"],
            ImportIssueModel.issue_source == "row_validation")) == 2

    mixed = b"day,ingredient,qty,unit\n2026-01-01,Milk,1,l\n2026-01-02,,2,l\n"
    created = client.post("/api/v1/imports", data={"store_id": "STORE_001", "forecast_date": "2026-01-01"},
        files={"files": ("partial.csv", mixed, "text/csv")}).json()
    assert client.post(f"/api/v1/imports/{created['import_id']}/confirm", json={"mappings": [{
        "sheet_id": created["sheets"][0]["sheet_id"], "sheet_type": "usage_history", "column_mapping": mapping}]}).status_code == 200
    partial = client.post(f"/api/v1/imports/{created['import_id']}/process?policy=partial_success")
    assert partial.status_code == 200 and partial.json()["processing_policy"] == "partial_success"
    assert len(partial.json()["issues"]) == 1

    preview_csv = b"day,ingredient,qty,unit\n2026-01-03,Sugar,1,kg\n"
    created = client.post("/api/v1/imports", data={"store_id": "STORE_001", "forecast_date": "2026-01-01"},
        files={"files": ("preview.csv", preview_csv, "text/csv")}).json()
    assert client.post(f"/api/v1/imports/{created['import_id']}/confirm", json={"mappings": [{
        "sheet_id": created["sheets"][0]["sheet_id"], "sheet_type": "usage_history", "column_mapping": mapping}]}).status_code == 200
    preview = client.post(f"/api/v1/imports/{created['import_id']}/process?policy=preview_only")
    assert preview.status_code == 200 and preview.json()["status"] in {"confirmed", "mapped"}
    with client.app.state.session_factory() as session:
        assert session.scalar(select(UsageDailyModel).where(UsageDailyModel.ingredient_id != None)) is not None
        assert session.scalar(select(IngredientModel).where(IngredientModel.normalized_name == "sugar")) is None


def test_remaining_canonical_fields_are_persisted(client):
    inventory = b"day,ingredient,qty,unit,batch,warehouse\n2026-01-01,Milk,10,l,MILK-001,Cold Room\n"
    assert upload_confirm_process(client, inventory, {"day":"snapshot_date","ingredient":"ingredient_name","qty":"on_hand","unit":"unit","batch":"batch_id","warehouse":"warehouse_name"}, "inventory")[1].status_code == 200
    usage = b"day,ingredient,qty,unit,source,waste\n2026-01-02,Milk,2,l,pos,0.25\n"
    assert upload_confirm_process(client, usage, {"day":"date","ingredient":"ingredient_name","qty":"quantity_used","unit":"unit","source":"source","waste":"waste_quantity"}, "usage_history")[1].status_code == 200
    recipe = b"product,ingredient,qty,unit,yield_qty,yield_unit,version,effective\nLatte,Milk,1,l,2,cup,1,2026-01-01\n"
    assert upload_confirm_process(client, recipe, {"product":"product_name","ingredient":"ingredient_name","qty":"ingredient_quantity","unit":"ingredient_unit","yield_qty":"yield_quantity","yield_unit":"yield_unit","version":"recipe_version","effective":"effective_date"}, "recipes")[1].status_code == 200
    purchase = b"day,ingredient,qty,unit,total,po\n2026-01-03,Milk,2,l,40000,PO-42\n"
    assert upload_confirm_process(client, purchase, {"day":"purchase_date","ingredient":"ingredient_name","qty":"quantity_received","unit":"unit","total":"total_cost","po":"purchase_order_id"}, "purchase_history")[1].status_code == 200
    with client.app.state.session_factory() as session:
        lot = session.scalar(select(InventoryLotModel)); usage_row = session.scalar(select(UsageDailyModel))
        recipe_row = session.scalar(select(RecipeVersionModel)); receipt = session.scalar(select(PurchaseReceiptModel))
        assert lot.warehouse_name == "Cold Room"
        assert usage_row.usage_source == "pos" and usage_row.waste_quantity == Decimal("0.250000")
        assert recipe_row.yield_quantity == Decimal("2.000000") and recipe_row.yield_unit == "cup" and recipe_row.version == 1
        assert receipt.total_cost == Decimal("40000.000000") and receipt.purchase_order_id == "PO-42"


def test_recipe_import_blank_version_auto_versions(client):
    mapping = {"product":"product_name", "ingredient":"ingredient_name", "qty":"ingredient_quantity", "unit":"ingredient_unit", "version":"recipe_version", "effective":"effective_date"}
    first = b"product,ingredient,qty,unit,version,effective\nAuto Tea,Sugar,1,kg,,2026-01-01\n"
    assert upload_confirm_process(client, first, mapping, "recipes", name="recipe-auto-1.csv")[1].status_code == 200
    second = b"product,ingredient,qty,unit,version,effective\nAuto Tea,Sugar,2,kg,,2026-02-01\n"
    assert upload_confirm_process(client, second, mapping, "recipes", name="recipe-auto-2.csv")[1].status_code == 200
    with client.app.state.session_factory() as session:
        product = session.scalar(select(ProductModel).where(ProductModel.normalized_name == "auto tea"))
        versions = list(session.scalars(select(RecipeVersionModel).where(RecipeVersionModel.product_id == product.product_id).order_by(RecipeVersionModel.version)))
        assert [item.version for item in versions] == [1, 2]


def test_recipe_import_semver_returns_structured_issue_not_500(client):
    mapping = {"product":"product_name", "ingredient":"ingredient_name", "qty":"ingredient_quantity", "unit":"ingredient_unit", "version":"recipe_version", "effective":"effective_date"}
    invalid = b"product,ingredient,qty,unit,version,effective\nSemver Tea,Sugar,1,kg,v1.2,2026-01-01\n"
    body, response = upload_confirm_process(client, invalid, mapping, "recipes", name="recipe-semver.csv")
    assert response.status_code == 422, response.text
    issue = response.json()["details"]["issues"][0]
    assert issue["code"] == "INVALID_RECIPE_VERSION"
    assert issue["field"] == "recipe_version"
    assert issue["raw_value"] == "v1.2"
    assert issue["sheet"] == "recipe-semver"
    assert issue["row_number"] == 2
    with client.app.state.session_factory() as session:
        stored = session.scalar(select(ImportIssueModel).where(
            ImportIssueModel.import_id == body["import_id"],
            ImportIssueModel.code == "INVALID_RECIPE_VERSION",
        ))
        assert stored is not None
        assert json.loads(stored.details_json)["raw_value"] == "v1.2"


def test_delivery_schedule_normalization_and_arrival_adjustment():
    assert normalize_delivery_days("Monday, Wednesday, 6") == [0, 2, 6]
    service = __import__("app.services.procurement_planning_service", fromlist=["ProcurementPlanningService"]).ProcurementPlanningService(SimpleNamespace())
    term = SimpleNamespace(lead_time_days=0, available_delivery_days=json.dumps([2]))
    assert service._delivery_date(term, date(2026, 8, 3)) == (date(2026, 8, 5), True)
    unavailable = SimpleNamespace(lead_time_days=0, available_delivery_days="[]")
    assert service._delivery_date(unavailable, date(2026, 8, 3))[0] is None


@pytest.mark.parametrize("raw,expected", [
    ("thùng", "case"), ("case", "case"), ("bao", "bag"),
    ("bag", "bag"), ("gói", "pack"), ("pack", "pack"),
    ("hộp", "box"),
])
def test_packaging_unit_normalization(raw, expected):
    assert normalize_packaging_unit(raw) == expected


def test_packaging_unit_is_not_a_physical_unit_alias():
    with pytest.raises(ValidationError):
        normalize_unit("case")
    with pytest.raises(ValidationError):
        validate_compatible("case", "liter")


def test_supplier_rule_mapping_requires_packaging_and_physical_base_fields():
    columns = [
        "Vendor", "Material", "MOQ", "Order UOM", "Pack Size",
        "Base UOM", "Lead time (days)", "Giá mua", "Lịch giao",
    ]
    profile = SheetProfile(
        file_name="vendor.xlsx", sheet_name="Vendor Rules",
        header_row_zero_based=0, row_count=1, column_count=len(columns),
        columns=columns, dtypes={column: "object" for column in columns},
        sample_rows=[],
    )
    result = map_sheet_rules(profile)
    assert result.column_mapping == {
        "Vendor": "supplier_name",
        "Material": "ingredient_name",
        "MOQ": "minimum_order_quantity",
        "Order UOM": "order_unit",
        "Pack Size": "package_size",
        "Base UOM": "package_base_unit",
        "Lead time (days)": "lead_time_days",
        "Giá mua": "unit_price",
        "Lịch giao": "available_delivery_days",
    }
    assert result.confidence == 1.0
    assert result.requires_review is False

    incomplete = SheetProfile(
        file_name="vendor.xlsx", sheet_name="Vendor Rules",
        header_row_zero_based=0, row_count=1, column_count=4,
        columns=columns[:4], dtypes={column: "object" for column in columns[:4]},
        sample_rows=[],
    )
    suggestion = map_sheet_rules(incomplete)
    assert suggestion.requires_review is True
    assert any("package_size" in warning for warning in suggestion.warnings)
    assert any("package_base_unit" in warning for warning in suggestion.warnings)


def test_supplier_packaging_import_converts_only_package_size(client):
    mapping = {
        "supplier": "supplier_name", "ingredient": "ingredient_name",
        "moq": "minimum_order_quantity", "order": "order_unit",
        "pack": "package_size", "base": "package_base_unit", "price": "unit_price", "lead": "lead_time_days",
        "lead": "lead_time_days", "cost": "unit_price",
    }
    csv = (
        "supplier,ingredient,moq,order,pack,base,lead,cost\n"
        "A,Milk,1,thùng,12,liter,2,100\n"
        "B,Flour,2,bao,5000,g,1,200\n"
        "C,Cups,1,gói,1000,piece,0,0\n"
    ).encode("utf-8")
    body, response = upload_confirm_process(
        client, csv, mapping, "supplier_constraints",
        name="supplier-packaging.csv",
    )
    assert response.status_code == 200, response.text
    result = client.get(f"/api/v1/imports/{body['import_id']}/result")
    assert result.status_code == 200
    with client.app.state.session_factory() as session:
        terms = {
            term.order_unit: term
            for term in session.scalars(select(SupplierIngredientTermModel))
        }
        assert terms["case"].unit == "lít"
        assert terms["case"].pack_size == Decimal("12.000000")
        assert terms["case"].moq == Decimal("12.000000")
        assert terms["bag"].unit == "g"
        assert terms["bag"].pack_size == Decimal("5000.000000")
        assert terms["bag"].moq == Decimal("10000.000000")
        assert terms["pack"].unit == "cái"
        assert terms["pack"].pack_size == Decimal("1000.000000")
        assert terms["pack"].moq == Decimal("1000.000000")


def test_supplier_package_size_converts_to_existing_ingredient_base(client):
    created = client.post("/api/v1/stores/STORE_001/ingredients", json={
        "ingredient": "Flour", "sku": "FLOUR", "base_unit": "kg",
        "active": True,
    })
    assert created.status_code == 201
    mapping = {
        "supplier": "supplier_name", "ingredient": "ingredient_name",
        "moq": "minimum_order_quantity", "order": "order_unit",
        "pack": "package_size", "base": "package_base_unit",
        "price": "unit_price", "lead": "lead_time_days",
    }
    csv = (
        b"supplier,ingredient,moq,order,pack,base,price,lead\n"
        b"ABC,Flour,2,bag,5000,g,0,0\n"
    )
    _, response = upload_confirm_process(
        client, csv, mapping, "supplier_constraints"
    )
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        term = session.scalar(select(SupplierIngredientTermModel))
        assert term.order_unit == "bag"
        assert term.pack_size == Decimal("5.000000")
        assert term.moq == Decimal("10.000000")
        assert term.unit == "kg"


def test_supplier_incompatible_base_unit_rolls_back_and_failed_is_not_retryable(client):
    created = client.post("/api/v1/stores/STORE_001/ingredients", json={
        "ingredient": "Flour", "sku": "FLOUR", "base_unit": "kg",
        "active": True,
    })
    assert created.status_code == 201
    mapping = {
        "supplier": "supplier_name", "ingredient": "ingredient_name",
        "moq": "minimum_order_quantity", "order": "order_unit",
        "pack": "package_size", "base": "package_base_unit",
    }
    csv = (
        b"supplier,ingredient,moq,order,pack,base\n"
        b"ABC,Flour,1,case,12,liter\n"
    )
    body, response = upload_confirm_process(
        client, csv, mapping, "supplier_constraints"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    retry = client.post(f"/api/v1/imports/{body['import_id']}/process")
    assert retry.status_code == 409
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(SupplierIngredientTermModel)
        ) == 0


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


def test_business_constraint_import_resolves_ingredient_and_is_versioned_idempotently(client):
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(ingredient_id="milk-constraint", store_id="STORE_001", ingredient="Milk constraint",
            normalized_name="milk constraint", base_unit="lít", active=True, source="test")); session.commit()
    mapping = {"kind": "constraint_type", "ingredient": "ingredient_name", "value": "value", "unit": "unit", "effective": "effective_date"}
    first = b"kind,ingredient,value,unit,effective\nsafety_stock,Milk constraint,12000,ml,2026-07-01\n"
    _, response = upload_confirm_process(client, first, mapping, "business_constraints", name="constraint.csv")
    assert response.status_code == 200, response.text
    _, response = upload_confirm_process(client, first, mapping, "business_constraints", name="constraint-same.csv")
    assert response.status_code == 200, response.text
    changed = b"kind,ingredient,value,unit,effective\nsafety_stock,Milk constraint,13,liter,2026-08-01\n"
    _, response = upload_confirm_process(client, changed, mapping, "business_constraints", name="constraint-v2.csv")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        rows = list(session.scalars(select(InventoryConstraintModel).order_by(InventoryConstraintModel.version)))
        assert len(rows) == 2
        assert rows[0].active is False and rows[0].end_date == date(2026, 7, 31)
        assert rows[1].active is True and rows[1].version == 2 and rows[1].ingredient_id == "milk-constraint"


def test_business_constraint_import_supports_type_aware_units_and_api(client):
    with client.app.state.session_factory() as session:
        session.add(IngredientModel(ingredient_id="typed-milk", store_id="STORE_001", ingredient="Typed milk",
            normalized_name="typed milk", base_unit=normalize_unit("liter"), active=True, source="test")); session.commit()
    mapping = {"kind": "constraint_type", "ingredient": "ingredient_name", "value": "value", "unit": "unit", "effective": "effective_date"}
    csv = ("kind,ingredient,value,unit,effective\n"
        "safety_stock,Typed milk,12000,ml,2026-07-01\n"
        "maximum_stock,Typed milk,40,liter,2026-07-01\n"
        "shelf_life_target,Typed milk,7,days,2026-07-01\n"
        "service_level_target,,95,percent,2026-07-01\n"
        "storage_capacity,,1000,liter,2026-07-01\n").encode()
    _, response = upload_confirm_process(client, csv, mapping, "business_constraints", name="typed-constraints.csv")
    assert response.status_code == 200, response.text
    _, response = upload_confirm_process(client, csv, mapping, "business_constraints", name="typed-constraints-same.csv")
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        rows = list(session.scalars(select(InventoryConstraintModel).where(InventoryConstraintModel.store_id == "STORE_001")))
        assert len(rows) == 5
        by_type = {row.constraint_type: row for row in rows}
        assert (by_type["shelf_life_target"].value, by_type["shelf_life_target"].unit) == (7, "day")
        assert (by_type["service_level_target"].value, by_type["service_level_target"].unit) == (Decimal("0.95"), "ratio")
        assert by_type["safety_stock"].unit == "ml" and by_type["maximum_stock"].unit == normalize_unit("liter")
    api = client.get("/api/v1/stores/STORE_001/inventory-constraints")
    assert api.status_code == 200
    items = {item["constraint_type"]: item for item in api.json()["items"]}
    assert items["shelf_life_target"]["value"] == "7.000000" and items["shelf_life_target"]["unit"] == "day"
    assert items["service_level_target"]["value"] == "0.950000" and items["service_level_target"]["unit"] == "ratio"
