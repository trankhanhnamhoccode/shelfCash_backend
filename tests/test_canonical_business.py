from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.names import display_name, normalize_name
from app.core.units import convert_quantity, normalize_unit, unit_dimension
from app.models.business import (
    CalendarFeatureModel, IngredientAliasModel, IngredientModel, InventoryLotModel,
    InventoryMovementModel, ProductModel, PurchaseReceiptModel, SalesDailyModel,
    StoreSettingsModel, SupplierIngredientTermModel, UsageDailyModel,
)
from app.repositories.business import (
    CatalogRepository, HistoryRepository, InventoryRepository, SupplierTermRepository,
)
from app.repositories.recipes import RecipeRepository
from app.services.entity_resolution import EntityResolutionService
from app.services.recipe_service import RecipeVersionService
from scripts.seed_database import seed_database
from app.db.session import create_engine_from_url, create_session_factory
from app.models.import_normalized import ImportJobModel


BUSINESS_TABLES = {
    "store_settings", "ingredients", "ingredient_aliases", "products",
    "recipe_versions", "recipe_lines", "suppliers", "supplier_ingredient_terms",
    "inventory_lots", "inventory_movements", "sales_daily", "usage_daily",
    "purchase_receipts", "calendar_features",
}


@pytest.fixture
def seeded(session_factory):
    seed_database(session_factory)
    return session_factory


def test_business_tables_are_in_migrated_database(session_factory):
    with session_factory() as session:
        assert BUSINESS_TABLES <= set(inspect(session.bind).get_table_names())
        assert {"imports", "import_jobs", "import_sheet_profiles"} <= set(inspect(session.bind).get_table_names())


def test_migration_empty_0002_populated_and_downgrade(tmp_path):
    def config_for(path):
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
        return config

    empty = tmp_path / "empty.db"
    command.upgrade(config_for(empty), "head")
    assert BUSINESS_TABLES <= set(inspect(create_engine(f"sqlite:///{empty.as_posix()}")).get_table_names())

    old = tmp_path / "old.db"
    old_config = config_for(old)
    command.upgrade(old_config, "20260728_0003")
    engine = create_engine_from_url(f"sqlite:///{old.as_posix()}")
    factory = create_session_factory(engine)
    seed_database(factory)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO import_jobs "
            "(import_id, store_id, forecast_horizon, status, requires_review, created_at, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("00000000-0000-0000-0000-000000000003", "STORE_001", 7,
             "completed", 0, datetime.now(timezone.utc), "{}"),
        )
        connection.exec_driver_sql(
            "INSERT INTO ingredients "
            "(ingredient_id, store_id, ingredient, normalized_name, base_unit, active, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("00000000-0000-0000-0000-000000000004", "STORE_001", "Legacy flour",
             "legacy flour", "kg", 1, "manual", datetime.now(timezone.utc),
             datetime.now(timezone.utc)),
        )
    engine.dispose()
    command.upgrade(old_config, "head")
    engine = create_engine(f"sqlite:///{old.as_posix()}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT status FROM import_jobs WHERE import_id = ?",
            ("00000000-0000-0000-0000-000000000003",),
        ).scalar_one() == "completed"
        assert connection.exec_driver_sql(
            "SELECT ingredient FROM ingredients WHERE ingredient_id = ?",
            ("00000000-0000-0000-0000-000000000004",),
        ).scalar_one() == "Legacy flour"
    command.downgrade(old_config, "20260728_0003")
    tables = set(inspect(engine).get_table_names())
    assert BUSINESS_TABLES <= tables
    assert {"imports", "import_jobs", "import_sheet_profiles"} <= tables
    engine.dispose()


def test_unit_and_name_normalization_are_deterministic():
    assert normalize_unit(" Litres ") == "lít"
    assert normalize_unit("PCS") == "cái"
    assert unit_dimension("kilogram") == "mass"
    assert convert_quantity(Decimal("1.234567"), "kg", "g") == Decimal("1234.567000")
    assert convert_quantity("1.25", "lít", "ml") == Decimal("1250.00")
    assert display_name("  Sữa   tươi  ") == "Sữa tươi"
    assert normalize_name(" SỮA tươi ") == normalize_name("sữa TƯƠI")
    with pytest.raises(ValidationError):
        normalize_unit("box")
    with pytest.raises(ValidationError):
        convert_quantity(1, "kg", "lít")


def test_catalog_resolution_order_and_store_isolation(seeded):
    with seeded() as session:
        catalog = CatalogRepository(session)
        a = catalog.add_ingredient("STORE_001", "Sữa tươi", "lít", sku="MILK")
        b = catalog.add_ingredient("STORE_TEST_001", "Sữa tươi", "lít", sku="MILK")
        session.flush()
        catalog.add_alias("STORE_001", a.ingredient_id, "Milk")
        session.flush()
        resolver = EntityResolutionService(catalog)
        assert resolver.ingredient("STORE_001", sku="MILK").ingredient_id == a.ingredient_id
        assert resolver.ingredient("STORE_001", name="milk").ingredient_id == a.ingredient_id
        assert resolver.ingredient("STORE_TEST_001", name="Sữa tươi").ingredient_id == b.ingredient_id
        with pytest.raises(ResourceNotFoundError):
            resolver.ingredient("STORE_TEST_001", name="milk")
        created = resolver.ingredient("STORE_001", name="Đường", base_unit="kg", create_if_missing=True)
        assert created.source == "import"


def test_database_uniqueness_and_settings_constraints(seeded):
    with seeded() as session:
        repo = CatalogRepository(session)
        repo.add_ingredient("STORE_001", "Muối", "g", sku="SALT")
        session.flush()
        repo.add_ingredient("STORE_001", " MUỐI ", "g")
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        session.add(StoreSettingsModel(setting_id=str(uuid4()), store_id="STORE_001", monthly_budget=-1, forecast_horizon=7, default_strategy="balanced", version=1))
        with pytest.raises(IntegrityError):
            session.flush()


def _catalog(session):
    repo = CatalogRepository(session)
    ingredient = repo.add_ingredient("STORE_001", "Cà phê", "kg")
    product = repo.add_product("STORE_001", "Cà phê sữa", sku="CF")
    supplier = repo.add_supplier("STORE_001", "ABC Food")
    session.flush()
    return repo, ingredient, product, supplier


def test_recipe_hash_versions_and_previous_effective_to(seeded):
    with seeded() as session:
        _, ingredient, product, _ = _catalog(session)
        service = RecipeVersionService(RecipeRepository(session))
        lines = [{"ingredient_id": ingredient.ingredient_id, "quantity": "0.1", "unit": "kg"}]
        assert service.compute_content_hash(product.product_id, date(2026, 1, 1), lines) == service.compute_content_hash(product.product_id, date(2026, 1, 1), list(reversed(lines)))
        v1 = service.create_version("STORE_001", product.product_id, date(2026, 1, 1), lines)
        same = service.create_version("STORE_001", product.product_id, date(2026, 1, 1), lines)
        assert same.recipe_version_id == v1.recipe_version_id
        v2 = service.create_version("STORE_001", product.product_id, date(2026, 2, 1), [{"ingredient_id": ingredient.ingredient_id, "quantity": "0.2", "unit": "kg"}])
        assert v2.version == 2
        assert v1.effective_to == date(2026, 1, 31)


def test_recipe_rejects_duplicate_cross_store_dimension_and_backdate(seeded):
    with seeded() as session:
        repo, ingredient, product, _ = _catalog(session)
        other = repo.add_ingredient("STORE_TEST_001", "Nước", "lít")
        session.flush()
        service = RecipeVersionService(RecipeRepository(session))
        with pytest.raises(ValidationError):
            service.create_version("STORE_001", product.product_id, date.today(), [{"ingredient_id": ingredient.ingredient_id, "quantity": 1, "unit": "kg"}] * 2)
        with pytest.raises(ValidationError):
            service.create_version("STORE_001", product.product_id, date.today(), [{"ingredient_id": other.ingredient_id, "quantity": 1, "unit": "lít"}])
        with pytest.raises(ValidationError):
            service.create_version("STORE_001", product.product_id, date.today(), [{"ingredient_id": ingredient.ingredient_id, "quantity": 1, "unit": "ml"}])


def test_inventory_balance_and_transaction_rollback(seeded):
    lot_id = str(uuid4())
    with seeded() as session:
        _, ingredient, _, _ = _catalog(session)
        inventory = InventoryRepository(session)
        inventory.add_lot(InventoryLotModel(lot_id=lot_id, store_id="STORE_001", ingredient_id=ingredient.ingredient_id, received_date=date.today(), initial_quantity=Decimal("10"), unit="kg", source="manual", version=1))
        inventory.add_movement(InventoryMovementModel(movement_id=str(uuid4()), store_id="STORE_001", lot_id=lot_id, movement_type="opening_balance", quantity_delta=Decimal("10"), unit="kg", occurred_at=datetime.now(timezone.utc), source="manual"))
        inventory.add_movement(InventoryMovementModel(movement_id=str(uuid4()), store_id="STORE_001", lot_id=lot_id, movement_type="usage", quantity_delta=Decimal("-2.25"), unit="kg", occurred_at=datetime.now(timezone.utc), source="manual"))
        session.flush()
        assert inventory.calculate_lot_balance("STORE_001", lot_id) == Decimal("7.750000")
        session.rollback()
    with seeded() as session:
        assert session.get(InventoryLotModel, lot_id) is None


def test_cross_store_supplier_term_lot_and_history_rejected(seeded):
    with seeded() as session:
        repo, ingredient, product, supplier = _catalog(session)
        other = repo.add_ingredient("STORE_TEST_001", "Khác", "kg")
        session.flush()
        terms = SupplierTermRepository(session)
        with pytest.raises(ValidationError):
            terms.add(SupplierIngredientTermModel(constraint_id=str(uuid4()), store_id="STORE_001", supplier_id=supplier.supplier_id, ingredient_id=other.ingredient_id, unit_cost=1, moq=0, pack_size=1, lead_time_days=0, unit="kg", version=1, source="manual"))
        with pytest.raises(ValidationError):
            InventoryRepository(session).add_lot(InventoryLotModel(lot_id=str(uuid4()), store_id="STORE_001", ingredient_id=other.ingredient_id, received_date=date.today(), initial_quantity=0, unit="kg", source="manual", version=1))
        history = HistoryRepository(session)
        with pytest.raises(ValidationError):
            history.add_usage(UsageDailyModel(usage_record_id=str(uuid4()), store_id="STORE_001", date=date.today(), ingredient_id=other.ingredient_id, quantity=1, unit="kg", source="manual"))


def test_history_range_and_provenance_foundation(seeded):
    with seeded() as session:
        _, ingredient, product, supplier = _catalog(session)
        history = HistoryRepository(session)
        sale = SalesDailyModel(sales_record_id=str(uuid4()), store_id="STORE_001", date=date(2026, 1, 2), product_id=product.product_id, quantity=2, source="manual")
        history.add_sales(sale)
        receipt = PurchaseReceiptModel(receipt_id=str(uuid4()), store_id="STORE_001", ingredient_id=ingredient.ingredient_id, supplier_id=supplier.supplier_id, receipt_date=date(2026, 1, 3), quantity=1, unit="kg", source="manual")
        history.add_receipt(receipt)
        session.flush()
        assert history.range("sales", "STORE_001", date(2026, 1, 1), date(2026, 1, 31)) == [sale]
        assert not history.exists_by_provenance("sales", "x", "y", "z")


def test_normal_import_process_does_not_create_business_rows(client):
    response = client.post("/api/v1/imports", data={"store_id": "STORE_001"}, files={"files": ("sales.csv", b"date,product,quantity\n2026-01-01,Coffee,1\n", "text/csv")})
    assert response.status_code == 201
    with client.app.state.session_factory() as session:
        for model in (IngredientModel, ProductModel, InventoryLotModel, SalesDailyModel, UsageDailyModel, PurchaseReceiptModel):
            assert session.scalar(select(model).limit(1)) is None
