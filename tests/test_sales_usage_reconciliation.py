from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models.business import IngredientModel, ProductModel, RecipeLineModel, RecipeVersionModel, SalesDailyModel, UsageDailyModel
from app.services.sales_usage_reconciliation import DERIVED_USAGE_SOURCE, reconcile_usage_from_sales


DAY = date(2026, 8, 12)


def _product(client, sku):
    return client.post("/api/v1/stores/STORE_001/products", json={
        "product": sku, "sku": sku, "price": 100, "active": True,
    }).json()


def _recipe(session_factory, product_id, ingredient_id, quantity, effective_from=date(2026, 1, 1)):
    with session_factory() as s:
        version = str(uuid4())
        next_version = len(list(s.scalars(select(RecipeVersionModel).where(RecipeVersionModel.product_id == product_id)))) + 1
        s.add(RecipeVersionModel(recipe_version_id=version, store_id="STORE_001", product_id=product_id, version=next_version, effective_from=effective_from, content_hash=str(uuid4()).replace("-", ""), source="test"))
        s.add(RecipeLineModel(recipe_line_id=str(uuid4()), recipe_version_id=version, ingredient_id=ingredient_id, quantity=quantity, unit="kg"))
        s.commit()


def _ingredient(session_factory, ident="usage-ing"):
    with session_factory() as s:
        s.add(IngredientModel(ingredient_id=ident, store_id="STORE_001", ingredient=ident, normalized_name=ident, base_unit="kg", active=True, source="test", version=1)); s.commit()


def _usage(session_factory, ingredient_id="usage-ing"):
    with session_factory() as s:
        return s.scalar(select(UsageDailyModel).where(UsageDailyModel.store_id=="STORE_001", UsageDailyModel.date==DAY, UsageDailyModel.ingredient_id==ingredient_id))


def test_api_replay_rebuilds_once_and_explicit_usage_wins(client, session_factory):
    _ingredient(session_factory); product = _product(client, "USAGE-API"); _recipe(session_factory, product["product_id"], "usage-ing", 1)
    body = {"source":"pos", "records":[{"external_record_id":"usage-api-1","date":DAY.isoformat(),"product_id":product["product_id"],"quantity":"10","unit_price":100,"promotion":False}]}
    assert client.post("/api/v1/stores/STORE_001/sales-history/batch", json=body, headers={"Idempotency-Key":"usage-api"}).status_code == 201
    assert client.post("/api/v1/stores/STORE_001/sales-history/batch", json=body, headers={"Idempotency-Key":"usage-api"}).status_code == 201
    assert _usage(session_factory).quantity == Decimal("10")
    with session_factory() as s:
        row = _usage(session_factory); row.quantity = Decimal("14"); row.source = "import"; s.merge(row); s.commit()
    with session_factory() as s: reconcile_usage_from_sales(s, "STORE_001", {DAY}); s.commit()
    row = _usage(session_factory)
    assert row.quantity == Decimal("14") and row.source == "import"


def test_rebuild_aggregates_products_and_promotion_segments_without_additive_drift(client, session_factory):
    _ingredient(session_factory); a, b = _product(client, "USAGE-A"), _product(client, "USAGE-B")
    _recipe(session_factory, a["product_id"], "usage-ing", 1); _recipe(session_factory, b["product_id"], "usage-ing", 2)
    with session_factory() as s:
        s.add_all([
            SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=DAY, product_id=a["product_id"], quantity=50, unit_price=100, promotion=False, source="import"),
            SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=DAY, product_id=a["product_id"], quantity=20, unit_price=100, promotion=True, source="import"),
            SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=DAY, product_id=b["product_id"], quantity=5, unit_price=100, promotion=False, source="import"),
        ]); reconcile_usage_from_sales(s, "STORE_001", {DAY}); s.commit()
    assert _usage(session_factory).quantity == Decimal("80")
    with session_factory() as s:
        promo=s.scalar(select(SalesDailyModel).where(SalesDailyModel.product_id==a["product_id"], SalesDailyModel.promotion.is_(True))); promo.quantity=30
        reconcile_usage_from_sales(s, "STORE_001", {DAY}); s.commit()
    assert _usage(session_factory).quantity == Decimal("90")


def test_rebuild_uses_effective_recipe_and_missing_recipe_does_not_fabricate_usage(client, session_factory):
    _ingredient(session_factory); product = _product(client, "USAGE-VERSIONED"); missing = _product(client, "USAGE-MISSING")
    _recipe(session_factory, product["product_id"], "usage-ing", 1, date(2026, 1, 1)); _recipe(session_factory, product["product_id"], "usage-ing", 2, date(2026, 8, 1))
    with session_factory() as s:
        s.add_all([
            SalesDailyModel(sales_record_id=str(uuid4()),store_id="STORE_001",date=DAY,product_id=product["product_id"],quantity=3,unit_price=100,promotion=False,source="import"),
            SalesDailyModel(sales_record_id=str(uuid4()),store_id="STORE_001",date=DAY,product_id=missing["product_id"],quantity=9,unit_price=100,promotion=False,source="import"),
        ]); warnings=reconcile_usage_from_sales(s,"STORE_001",{DAY}); s.commit()
    assert _usage(session_factory).quantity == Decimal("6")
    assert any(x["code"] == "RECIPE_NOT_FOUND" for x in warnings)
