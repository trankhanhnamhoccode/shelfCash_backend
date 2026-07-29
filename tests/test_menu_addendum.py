from io import BytesIO
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from openpyxl import Workbook
from sqlalchemy import func, inspect, select

from app.core.canonical_schemas import CANONICAL_SCHEMAS, SHEET_TYPES
from app.core.exceptions import ValidationError
from app.core.menu import (
    normalize_item_type, normalize_menu_status, parse_combo_components,
)
from app.core.rule_mapper import map_sheet_rules
from app.models.business import (
    IngredientModel, InventoryLotModel, InventoryMovementModel, ProductBundleLineModel,
    ProductModel, RecipeLineModel, RecipeVersionModel, SalesDailyModel, UsageDailyModel,
)
from app.models.operations import (
    BudgetPeriodModel, ForecastRunModel, PlanRunModel, PurchaseOrderModel,
)
from app.schemas.llm import SheetProfile
from tests.conftest import migrate_database


HEADERS = [
    "Mã món", "Loại", "Tên món / Combo", "Thành phần combo", "ĐVT",
    "Tổng giá lẻ", "Mức giảm", "Giá bán", "Tiết kiệm", "Trạng thái",
]


def menu_workbook(*, missing_component=False):
    singles = [
        ("MON-001", "Cà phê sữa", 30000),
        ("MON-002", "Trà sữa trân châu", 38000),
        ("MON-003", "Sinh tố chuối", 35000),
        ("MON-004", "Cacao sữa", 36000),
        ("MON-005", "Nước cam", 32000),
    ]
    combos = [
        ("CMB-001", "Combo Một", "1 × Cà phê sữa + 1 × Trà sữa trân châu", 62000),
        ("CMB-002", "Combo Hai", "2 x Cà phê sữa + 1 X Sinh tố chuối", 85000),
        ("CMB-003", "Combo Ba", "1 * Cacao sữa + 1 × Nước cam", 60000),
        ("CMB-004", "Combo Bốn", "2 × Cà phê sữa + 1 × Nước cam", 80000),
        ("CMB-005", "Combo Năm", "1 × Cà phê sữa + 1 × Trà sữa trân châu + 1 × Sinh tố chuối", 95000),
    ]
    if missing_component:
        combos[-1] = ("CMB-005", "Combo Năm", "1 × Không tồn tại", 95000)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "06_Menu"
    sheet.append(HEADERS)
    for sku, name, price in singles:
        sheet.append([sku, "Món lẻ", name, "—", "ly", price, 0, price, 0, "Đang bán"])
    for sku, name, components, price in combos:
        sheet.append([sku, "Combo", name, components, "combo", None, None, price, None, "Đang bán"])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def upload_menu(client, content=None):
    response = client.post(
        "/api/v1/imports", data={"store_id": "STORE_001"},
        files={"files": ("06_Menu.xlsx", content or menu_workbook(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"Idempotency-Key": f"menu-{uuid4()}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm_and_process_menu(client, created):
    suggestion = created["suggested_mappings"][0]
    confirmed = client.post(
        f"/api/v1/imports/{created['import_id']}/confirm",
        json={"mappings": [{
            "profile_id": suggestion["profile_id"], "sheet_type": "menu",
            "column_mapping": suggestion["column_mapping"],
        }]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return client.post(f"/api/v1/imports/{created['import_id']}/process")


def create_product(client, sku, name, **overrides):
    payload = {
        "sku": sku, "product": name, "item_type": "single",
        "selling_unit": "ly", "price": 30000, "status": "active",
    }
    payload.update(overrides)
    return client.post("/api/v1/stores/STORE_001/products", json=payload)


def test_menu_schema_mapping_and_normalizers():
    assert "menu" in SHEET_TYPES
    assert CANONICAL_SCHEMAS["menu"] == {
        "fields": [
            "product_sku", "item_type", "product_name", "combo_components",
            "selling_unit", "list_price", "discount_rate", "selling_price",
            "savings_amount", "status",
        ],
        "core_fields": ["product_sku", "item_type", "product_name", "selling_price"],
    }
    profile = SheetProfile(
        file_name="06_Menu.xlsx", sheet_name="06_Menu",
        header_row_zero_based=0, row_count=10, column_count=10,
        columns=HEADERS, dtypes={header: "object" for header in HEADERS},
    )
    suggestion = map_sheet_rules(profile)
    assert suggestion.sheet_type == "menu"
    assert suggestion.column_mapping == dict(zip(HEADERS, CANONICAL_SCHEMAS["menu"]["fields"]))
    for value in ("Món lẻ", " Mon le ", "single", "retail"):
        assert normalize_item_type(value) == "single"
    for value in ("Combo", "bundle"):
        assert normalize_item_type(value) == "combo"
    for value in ("Đang bán", "ACTIVE", " enabled "):
        assert normalize_menu_status(value) == "active"
    for value in ("Ngừng bán", "inactive", "disabled"):
        assert normalize_menu_status(value) == "inactive"


def test_menu_mapping_gate_rejects_unmapped_and_duplicate(client):
    created = upload_menu(client)
    suggestion = created["suggested_mappings"][0]
    mapping = dict(suggestion["column_mapping"])
    mapping["Tiết kiệm"] = None
    response = client.post(
        f"/api/v1/imports/{created['import_id']}/confirm",
        json={"mappings": [{"profile_id": suggestion["profile_id"], "sheet_type": "menu",
                            "column_mapping": mapping}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MAPPING_INCOMPLETE"
    assert response.json()["details"]["unresolved_columns"] == ["Tiết kiệm"]
    assert client.post(f"/api/v1/imports/{created['import_id']}/process").status_code == 409

    mapping = dict(suggestion["column_mapping"])
    mapping["Giá bán"] = "list_price"
    duplicate = client.post(
        f"/api/v1/imports/{created['import_id']}/confirm",
        json={"mappings": [{"profile_id": suggestion["profile_id"], "sheet_type": "menu",
                            "column_mapping": mapping}]},
    )
    assert duplicate.status_code == 422
    assert "list_price" in duplicate.json()["details"]["duplicate_target_fields"]
    assert "selling_price" in duplicate.json()["details"]["missing_core_fields"]


@pytest.mark.parametrize("text,expected", [
    ("1 × Coffee", [(1, "Coffee")]),
    ("2 x Coffee + 1 X Tea", [(2, "Coffee"), (1, "Tea")]),
    ("1 *  Sinh tố chuối ", [(1, "Sinh tố chuối")]),
])
def test_combo_parser_signs(text, expected):
    parsed = parse_combo_components(text)
    assert [(item.quantity, item.product_name) for item in parsed] == expected


@pytest.mark.parametrize("text", ["Coffee", "0 x Coffee", "-1 x Coffee", "1 x "])
def test_combo_parser_rejects_invalid_syntax(text):
    with pytest.raises(ValidationError):
        parse_combo_components(text)


def test_combo_parser_rejects_duplicate_and_more_than_20():
    with pytest.raises(ValidationError) as duplicate:
        parse_combo_components("1 x Coffee + 2 x coffee")
    assert duplicate.value.details["code"] == "COMBO_COMPONENT_DUPLICATE"
    with pytest.raises(ValidationError):
        parse_combo_components(" + ".join(f"1 x P{i}" for i in range(21)))


def test_menu_import_happy_path_transaction_result_bootstrap_and_replay(client, session_factory):
    before_counts = {}
    side_effect_models = [
        InventoryLotModel, InventoryMovementModel, BudgetPeriodModel, PurchaseOrderModel,
        SalesDailyModel, UsageDailyModel, RecipeVersionModel, ForecastRunModel, PlanRunModel,
    ]
    with session_factory() as session:
        before_counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in side_effect_models
        }
    created = upload_menu(client)
    processed = confirm_and_process_menu(client, created)
    assert processed.status_code == 200, processed.text
    replay = client.post(f"/api/v1/imports/{created['import_id']}/process")
    assert replay.status_code == 200
    result = client.get(f"/api/v1/imports/{created['import_id']}/result").json()
    assert len(result["menu"]) == 10
    menu = client.get("/api/v1/stores/STORE_001/menu?status=all").json()
    assert menu["total"] == 10
    assert menu["summary"] == {
        "single_count": 5, "combo_count": 5, "active_count": 10, "inactive_count": 0,
    }
    assert sum(len(item["components"]) for item in menu["items"]) == 11
    bootstrap = client.get("/api/v1/stores/STORE_001/bootstrap").json()
    assert len(bootstrap["menu"]) == 10
    assert bootstrap["data_freshness"]["menu_updated_at"] is not None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductModel)) == 10
        assert session.scalar(select(func.count()).select_from(ProductBundleLineModel)) == 11
        after = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in side_effect_models
        }
    assert after == before_counts


def test_menu_import_rolls_back_all_products_when_component_missing(client, session_factory):
    created = upload_menu(client, menu_workbook(missing_component=True))
    response = confirm_and_process_menu(client, created)
    assert response.status_code == 422
    assert response.json()["code"] == "COMBO_COMPONENT_NOT_FOUND"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductModel)) == 0
        assert session.scalar(select(func.count()).select_from(ProductBundleLineModel)) == 0


def test_formula_without_cached_value_is_rejected(client):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "06_Menu"
    sheet.append(HEADERS)
    sheet.append(["A", "Món lẻ", "Coffee", "—", "ly", 1, 0, "=1+1", 0, "Đang bán"])
    output = BytesIO(); workbook.save(output)
    response = client.post(
        "/api/v1/imports", data={"store_id": "STORE_001"},
        files={"files": ("formula.xlsx", output.getvalue(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "FORMULA_VALUE_UNAVAILABLE"
    assert set(response.json()["details"]) == {"file_name", "sheet_name", "row_number", "column"}


def test_menu_product_crud_components_filters_isolation_and_recipe(client):
    first = create_product(client, "MON-A", "Coffee")
    second = create_product(client, "MON-B", "Tea", status="inactive")
    assert first.status_code == second.status_code == 201
    combo_body = {
        "sku": "CMB-A", "product": "Combo A", "item_type": "combo",
        "selling_unit": "combo", "price": 50000, "status": "active",
        "components": [{"component_product_id": first.json()["product_id"], "quantity": 2}],
    }
    combo = client.post("/api/v1/stores/STORE_001/products", json=combo_body,
                        headers={"Idempotency-Key": "combo-create"})
    replay = client.post("/api/v1/stores/STORE_001/products", json=combo_body,
                         headers={"Idempotency-Key": "combo-create"})
    assert combo.status_code == replay.status_code == 201
    assert combo.json()["product_id"] == replay.json()["product_id"]
    assert combo.json()["list_price"] == 60000
    assert client.get("/api/v1/stores/STORE_001/menu").json()["total"] == 2
    assert client.get("/api/v1/stores/STORE_001/menu?status=all").json()["total"] == 3
    assert client.get("/api/v1/stores/STORE_001/menu?status=all&item_type=combo").json()["total"] == 1
    assert client.get("/api/v1/stores/STORE_001/menu?status=all&search=CMB-A").json()["total"] == 1
    assert client.get("/api/v1/stores/STORE_TEST_001/menu?status=all").json()["total"] == 0
    assert client.put(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}/recipe",
        json={
            "version": 0,
            "effective_from": date.today().isoformat(),
            "lines": [{"ingredient_id": str(uuid4()), "quantity": 1, "unit": "g"}],
        },
    ).status_code == 409
    inactive_component = client.put(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}/components",
        json={"version": 1, "components": [{
            "component_product_id": second.json()["product_id"], "quantity": 1}]},
    )
    assert inactive_component.status_code == 422
    replaced_body = {"version": 1, "components": [{
        "component_product_id": first.json()["product_id"], "quantity": 3}]}
    replaced = client.put(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}/components",
        json=replaced_body, headers={"Idempotency-Key": "replace-components"})
    replaced_replay = client.put(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}/components",
        json=replaced_body, headers={"Idempotency-Key": "replace-components"})
    assert replaced.status_code == replaced_replay.status_code == 200
    assert replaced.json()["version"] == 2
    assert replaced.json()["components"][0]["position"] == 0
    stale = client.patch(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}",
        json={"version": 1, "price": 51000})
    assert stale.status_code == 409
    immutable = client.patch(
        f"/api/v1/stores/STORE_001/products/{combo.json()['product_id']}",
        json={"version": 2, "item_type": "single"})
    assert immutable.status_code == 409
    deactivate = client.patch(
        f"/api/v1/stores/STORE_001/products/{first.json()['product_id']}",
        json={"version": 1, "status": "inactive"})
    assert deactivate.status_code == 409


def test_product_validation_and_import_schema_discovery(client):
    assert create_product(client, "BAD-U", "Bad Unit", selling_unit="kg").status_code == 422
    assert create_product(client, "BAD-P", "Bad Price", price=0).status_code == 422
    assert client.post("/api/v1/stores/STORE_001/products", json={
        "sku": "CMB-X", "product": "Empty Combo", "item_type": "combo",
        "selling_unit": "combo", "price": 1, "status": "active",
    }).status_code == 422
    schemas = client.get("/api/v1/import-schemas")
    assert schemas.status_code == 200
    assert schemas.json()["schemas"]["menu"]["fields"] == CANONICAL_SCHEMAS["menu"]["fields"]


def test_combo_sales_expands_component_recipes_without_component_sales_or_inventory(client, session_factory):
    coffee = create_product(client, "MON-C", "Coffee").json()
    tea = create_product(client, "MON-T", "Tea").json()
    combo = client.post("/api/v1/stores/STORE_001/products", json={
        "sku": "CMB-S", "product": "Sales Combo", "item_type": "combo",
        "selling_unit": "combo", "price": 80000, "status": "active",
        "components": [
            {"component_product_id": coffee["product_id"], "quantity": 2},
            {"component_product_id": tea["product_id"], "quantity": 1},
        ],
    }).json()
    with session_factory() as session:
        ingredient = IngredientModel(
            ingredient_id=str(uuid4()), store_id="STORE_001", ingredient="Milk",
            normalized_name="milk", base_unit="ml", active=True, source="manual", version=1)
        session.add(ingredient)
        for product, amount in ((coffee, 100), (tea, 50)):
            version_id = str(uuid4())
            session.add(RecipeVersionModel(
                recipe_version_id=version_id, store_id="STORE_001",
                product_id=product["product_id"], version=1, effective_from=date(2026, 1, 1),
                content_hash=str(uuid4()).replace("-", ""), source="manual"))
            session.add(RecipeLineModel(
                recipe_line_id=str(uuid4()), recipe_version_id=version_id,
                ingredient_id=ingredient.ingredient_id, quantity=amount, unit="ml"))
        session.commit()
    body = {"source": "pos", "records": [{
        "external_record_id": "COMBO-SALE-1", "date": "2026-07-28",
        "product_id": combo["product_id"], "quantity": 2,
        "unit_price": 80000, "promotion": False,
    }]}
    first = client.post("/api/v1/stores/STORE_001/sales-history/batch", json=body,
                        headers={"Idempotency-Key": "combo-sales"})
    replay = client.post("/api/v1/stores/STORE_001/sales-history/batch", json=body,
                         headers={"Idempotency-Key": "combo-sales"})
    assert first.status_code == replay.status_code == 201
    with session_factory() as session:
        sales = list(session.scalars(select(SalesDailyModel)))
        assert len(sales) == 1 and sales[0].product_id == combo["product_id"]
        usage = session.scalar(select(UsageDailyModel))
        assert usage.quantity == Decimal("500.000000")
        assert session.scalar(select(func.count()).select_from(InventoryMovementModel)) == 0


def test_menu_migration_backfill_downgrade_reupgrade(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'menu-migration.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "20260728_0007")
    from sqlalchemy import create_engine
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO stores (store_id,store_name,timezone,currency,created_at,updated_at) "
            "VALUES ('S','Store','Asia/Ho_Chi_Minh','VND',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql(
            "INSERT INTO products (product_id,store_id,product,normalized_name,sku,price,active,source,version,created_at,updated_at) "
            "VALUES ('P','S','Legacy','legacy','LEG',1,1,'manual',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.exec_driver_sql("SELECT product,item_type FROM products WHERE product_id='P'").one()
        assert row == ("Legacy", "single")
        assert "product_bundle_lines" in inspect(connection).get_table_names()
    command.downgrade(config, "20260728_0007")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT product FROM products WHERE product_id='P'").scalar() == "Legacy"
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT item_type FROM products WHERE product_id='P'").scalar() == "single"
    engine.dispose()
