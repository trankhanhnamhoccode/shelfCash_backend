from decimal import Decimal
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy import func, select

from app.models.audit_log import AuditLogModel
from app.models.business import IngredientModel, ProductModel, RecipeLineModel, RecipeVersionModel
from app.db.session import create_engine_from_url, create_session_factory
from scripts.seed_database import seed_database


def ingredient_payload(**overrides):
    value = {"ingredient": "Sữa tươi", "sku": "MILK_001", "base_unit": "lít", "active": True}
    value.update(overrides)
    return value


def test_migration_0005_backfills_and_downgrades_without_data_loss(tmp_path):
    path = tmp_path / "catalog-migration.db"
    url = f"sqlite:///{path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260728_0004")
    engine = create_engine_from_url(url)
    factory = create_session_factory(engine)
    seed_database(factory)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO ingredients (ingredient_id,store_id,ingredient,normalized_name,base_unit,active,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("legacy-i", "STORE_001", "Legacy ingredient", "legacy ingredient", "kg", 1, "import", now, now),
        )
        connection.exec_driver_sql(
            "INSERT INTO products (product_id,store_id,product,normalized_name,active,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("legacy-p", "STORE_001", "Legacy product", "legacy product", 1, "import", now, now),
        )
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version FROM ingredients WHERE ingredient_id='legacy-i'").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT version FROM products WHERE product_id='legacy-p'").scalar_one() == 1
    engine.dispose()
    command.downgrade(config, "20260728_0004")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT ingredient FROM ingredients WHERE ingredient_id='legacy-i'").scalar_one() == "Legacy ingredient"
        assert connection.exec_driver_sql("SELECT product FROM products WHERE product_id='legacy-p'").scalar_one() == "Legacy product"
    assert {"ingredients", "products", "import_jobs"} <= set(inspect(engine).get_table_names())
    engine.dispose()


def product_payload(**overrides):
    value = {"product": "Sinh tố chuối", "sku": "BANANA", "price": 35000, "active": True}
    value.update(overrides)
    return value


def test_ingredient_crud_aliases_idempotency_and_request_id(client):
    endpoint = "/api/v1/stores/STORE_001/ingredients"
    assert client.get(endpoint).json() == []
    first = client.post(endpoint, json=ingredient_payload(), headers={"Idempotency-Key": "ingredient-1"})
    replay = client.post(endpoint, json=ingredient_payload(), headers={"Idempotency-Key": "ingredient-1"})
    assert first.status_code == replay.status_code == 201
    assert first.json()["ingredient_id"] == replay.json()["ingredient_id"]
    assert first.headers["X-Request-ID"]
    conflict = client.post(endpoint, json=ingredient_payload(ingredient="Khác"), headers={"Idempotency-Key": "ingredient-1"})
    assert conflict.status_code == 409
    duplicate = client.post(endpoint, json=ingredient_payload(sku="OTHER"))
    assert duplicate.status_code == 409
    item = first.json()
    listed = client.get(endpoint).json()
    assert listed[0]["aliases"] == []
    assert "normalized_name" not in listed[0]


def test_ingredient_patch_version_store_isolation_and_base_unit_policy(client):
    item = client.post("/api/v1/stores/STORE_001/ingredients", json=ingredient_payload()).json()
    endpoint = f"/api/v1/stores/STORE_001/ingredients/{item['ingredient_id']}"
    updated = client.patch(endpoint, json={"version": 1, "ingredient": "Sữa không đường", "active": False})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert not updated.json()["active"]
    stale = client.patch(endpoint, json={"version": 1, "active": True})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    cross = client.patch(f"/api/v1/stores/STORE_TEST_001/ingredients/{item['ingredient_id']}", json={"version": 2, "active": True})
    assert cross.status_code == 404
    allowed = client.patch(endpoint, json={"version": 2, "base_unit": "ml"})
    assert allowed.status_code == 200


def test_alias_duplicate_redundant_and_store_isolation(client):
    item = client.post("/api/v1/stores/STORE_001/ingredients", json=ingredient_payload()).json()
    assert client.get("/api/v1/stores/STORE_001/aliases").json() == []
    assert client.get("/api/v1/stores/STORE_TEST_001/aliases", params={"ingredient_id": item["ingredient_id"]}).status_code == 404
    endpoint = "/api/v1/stores/STORE_001/aliases"
    body = {"aliases": [{"ingredient_id": item["ingredient_id"], "source_name": "milk", "canonical_name": item["ingredient"]}]}
    first = client.put(endpoint, json=body, headers={"Idempotency-Key": "aliases-1"})
    assert first.status_code == 200
    assert first.json()[0]["alias"] == "milk"
    replay = client.put(endpoint, json=body, headers={"Idempotency-Key": "aliases-1"})
    assert replay.json()[0]["alias_id"] == first.json()[0]["alias_id"]
    assert len(client.get(endpoint).json()) == 1
    assert client.put(endpoint, json={"aliases": [{"ingredient_id": "missing", "source_name": "other", "canonical_name": item["ingredient"]}]}).status_code == 404


def test_products_validation_uniqueness_and_store_scope(client):
    endpoint = "/api/v1/stores/STORE_001/products"
    first = client.post(endpoint, json=product_payload(), headers={"Idempotency-Key": "product-1"})
    assert first.status_code == 201
    assert client.post(endpoint, json=product_payload(), headers={"Idempotency-Key": "product-1"}).json()["product_id"] == first.json()["product_id"]
    variant = client.post(endpoint, json=product_payload(sku="OTHER"))
    assert variant.status_code == 201
    assert variant.json()["product_id"] != first.json()["product_id"]
    other = client.post("/api/v1/stores/STORE_TEST_001/products", json=product_payload())
    assert other.status_code == 201
    assert client.post(endpoint, json=product_payload(product="No price", sku=None, price=None)).status_code == 201
    assert client.post(endpoint, json=product_payload(product="Bad", sku="BAD", price=-1)).status_code == 422
    listed = client.get(endpoint).json()
    assert len(listed) == 3 and all(x["store_id"] == "STORE_001" for x in listed)


def test_product_patch_version_audit_and_store_scope(client):
    created = client.post("/api/v1/stores/STORE_001/products", json=product_payload()).json()
    endpoint = f"/api/v1/stores/STORE_001/products/{created['product_id']}"
    updated = client.patch(endpoint, json={"version": 1, "product": "Sinh tố mới", "price": None})
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["price"] is None
    assert client.patch(endpoint, json={"version": 1, "active": False}).status_code == 409
    cross = f"/api/v1/stores/STORE_TEST_001/products/{created['product_id']}"
    assert client.patch(cross, json={"version": 2, "active": False}).status_code == 404
    with client.app.state.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(AuditLogModel).where(
                AuditLogModel.action == "product_updated"
            )
        ) == 1


def test_recipe_versions_decimal_conversion_conflict_and_base_unit_lock(client):
    ingredient = client.post("/api/v1/stores/STORE_001/ingredients", json=ingredient_payload(base_unit="kg")).json()
    product = client.post("/api/v1/stores/STORE_001/products", json=product_payload()).json()
    endpoint = f"/api/v1/stores/STORE_001/products/{product['product_id']}/recipe"
    assert client.get(endpoint).json()["recipe"] is None
    body = {"effective_from": "2026-01-01", "version": 0, "lines": [{"ingredient_id": ingredient["ingredient_id"], "quantity": "125.000000", "unit": "g"}]}
    first = client.put(endpoint, json=body, headers={"Idempotency-Key": "recipe-1"})
    assert first.status_code == 200
    recipe = first.json()["recipe"]
    assert recipe["lines"][0]["quantity"] == "0.125"
    assert recipe["lines"][0]["unit"] == "kg"
    replay = client.put(endpoint, json=body, headers={"Idempotency-Key": "recipe-1"})
    assert replay.json()["recipe"]["recipe_version_id"] == recipe["recipe_version_id"]
    reuse = client.put(endpoint, json={**body, "version": 1})
    assert reuse.json()["recipe"]["recipe_version_id"] == recipe["recipe_version_id"]
    changed = {**body, "effective_from": "2026-02-01", "version": 1, "lines": [{**body["lines"][0], "quantity": "250"}]}
    second = client.put(endpoint, json=changed)
    assert second.status_code == 200 and second.json()["recipe"]["version"] == 2
    assert client.put(endpoint, json={**changed, "effective_from": "2026-03-01"}).status_code == 409
    assert client.get(endpoint).json()["recipe"]["version"] == 2
    locked = client.patch(f"/api/v1/stores/STORE_001/ingredients/{ingredient['ingredient_id']}", json={"version": 1, "base_unit": "g"})
    assert locked.status_code == 409


def test_recipe_rejects_duplicates_cross_store_and_dimension(client):
    ingredient = client.post("/api/v1/stores/STORE_001/ingredients", json=ingredient_payload(base_unit="kg")).json()
    other = client.post("/api/v1/stores/STORE_TEST_001/ingredients", json=ingredient_payload(sku="OTHER")).json()
    product = client.post("/api/v1/stores/STORE_001/products", json=product_payload()).json()
    endpoint = f"/api/v1/stores/STORE_001/products/{product['product_id']}/recipe"
    base = {"effective_from": "2026-01-01", "version": 0}
    duplicate = {**base, "lines": [{"ingredient_id": ingredient["ingredient_id"], "quantity": "1", "unit": "kg"}] * 2}
    assert client.put(endpoint, json=duplicate).status_code == 422
    cross = {**base, "lines": [{"ingredient_id": other["ingredient_id"], "quantity": "1", "unit": "kg"}]}
    assert client.put(endpoint, json=cross).status_code == 404
    dimension = {**base, "lines": [{"ingredient_id": ingredient["ingredient_id"], "quantity": "1", "unit": "ml"}]}
    assert client.put(endpoint, json=dimension).status_code == 422
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RecipeVersionModel)) == 0


def test_openapi_contains_exact_new_route_set(client):
    expected = {
        ("get", "/api/v1/stores/{store_id}/ingredients"), ("post", "/api/v1/stores/{store_id}/ingredients"),
        ("patch", "/api/v1/stores/{store_id}/ingredients/{ingredient_id}"),
        ("get", "/api/v1/stores/{store_id}/aliases"),
        ("put", "/api/v1/stores/{store_id}/aliases"),
        ("get", "/api/v1/stores/{store_id}/products"), ("post", "/api/v1/stores/{store_id}/products"),
        ("patch", "/api/v1/stores/{store_id}/products/{product_id}"),
        ("get", "/api/v1/stores/{store_id}/products/{product_id}/recipe"),
        ("put", "/api/v1/stores/{store_id}/products/{product_id}/recipe"),
    }
    schema = client.get("/openapi.json").json()
    actual = {(method, path) for path, methods in schema["paths"].items() for method in methods}
    assert expected <= actual
    assert len(actual) >= 18


def test_wrong_checkpoint_3a1_routes_are_removed(client):
    assert client.get("/api/v1/ingredients").status_code == 404
    assert client.get("/api/v1/products").status_code == 404
    assert client.get("/api/v1/ingredient-aliases").status_code == 404
    assert client.get("/api/v1/products/unknown/recipes").status_code == 404
