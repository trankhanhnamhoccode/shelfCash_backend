from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.store import utc_now

UNIT_CHECK = "unit IN ('kg', 'g', 'lít', 'ml', 'cái')"


class StoreSettingsModel(Base):
    __tablename__ = "store_settings"
    __table_args__ = (
        CheckConstraint("monthly_budget >= 0", name="ck_store_settings_budget"),
        CheckConstraint("forecast_horizon >= 1", name="ck_store_settings_horizon"),
        CheckConstraint("default_strategy IN ('economy','balanced','safe')", name="ck_store_settings_strategy"),
        CheckConstraint("version >= 1", name="ck_store_settings_version"),
    )
    setting_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), unique=True, nullable=False)
    monthly_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forecast_horizon: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    default_strategy: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    safety_policy_json: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class IngredientModel(Base):
    __tablename__ = "ingredients"
    __table_args__ = (
        UniqueConstraint("store_id", "normalized_name", name="uq_ingredients_store_name"),
        UniqueConstraint("store_id", "sku", name="uq_ingredients_store_sku"),
        CheckConstraint("base_unit IN ('kg','g','lít','ml','cái')", name="ck_ingredients_base_unit"),
        Index("ix_ingredients_normalized_name", "normalized_name"),
        CheckConstraint("version >= 1", name="ck_ingredients_version"),
        Index("ix_ingredients_store_active", "store_id", "active"),
    )
    ingredient_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), index=True, nullable=False)
    ingredient: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(128))
    base_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class IngredientAliasModel(Base):
    __tablename__ = "ingredient_aliases"
    __table_args__ = (UniqueConstraint("store_id", "normalized_alias", name="uq_aliases_store_alias"),)
    alias_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ProductModel(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store_id", "sku", name="uq_products_store_sku"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_products_price"),
        CheckConstraint("item_type IN ('single','combo')", name="ck_products_item_type"),
        CheckConstraint("selling_unit IS NULL OR selling_unit IN ('ly','phần','chai','cái','combo')", name="ck_products_selling_unit"),
        Index("ix_products_normalized_name", "normalized_name"),
        Index("ix_products_store_normalized_name", "store_id", "normalized_name"),
        CheckConstraint("version >= 1", name="ck_products_version"),
        Index("ix_products_store_active", "store_id", "active"),
    )
    product_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), index=True, nullable=False)
    product: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(128))
    price: Mapped[int | None] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, default="single", server_default="single")
    selling_unit: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"), index=True)
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ProductBundleLineModel(Base):
    __tablename__ = "product_bundle_lines"
    __table_args__ = (
        UniqueConstraint("combo_product_id", "component_product_id", name="uq_bundle_combo_component"),
        CheckConstraint("quantity > 0", name="ck_bundle_line_quantity"),
        CheckConstraint("position >= 0", name="ck_bundle_line_position"),
        Index("ix_bundle_lines_store_id", "store_id"),
        Index("ix_bundle_lines_combo_product_id", "combo_product_id"),
        Index("ix_bundle_lines_component_product_id", "component_product_id"),
    )
    bundle_line_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    combo_product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False)
    component_product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.product_id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class SupplierModel(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("store_id", "normalized_name", name="uq_suppliers_store_name"),
        Index("ix_suppliers_normalized_name", "normalized_name"),
    )
    supplier_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), index=True, nullable=False)
    supplier: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RecipeVersionModel(Base):
    __tablename__ = "recipe_versions"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_recipe_product_version"),
        CheckConstraint("version >= 1", name="ck_recipe_version"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_recipe_dates"),
        Index("ix_recipe_product_effective", "product_id", "effective_from"),
        CheckConstraint("yield_quantity > 0", name="ck_recipe_yield_positive"),
        CheckConstraint("process_loss_rate >= 0 AND process_loss_rate < 1", name="ck_recipe_loss_rate"),
    )
    recipe_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.product_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    yield_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=1)
    yield_unit: Mapped[str | None] = mapped_column(String(16))
    process_loss_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RecipeLineModel(Base):
    __tablename__ = "recipe_lines"
    __table_args__ = (
        UniqueConstraint("recipe_version_id", "ingredient_id", name="uq_recipe_line_ingredient"),
        CheckConstraint("quantity > 0", name="ck_recipe_line_quantity"),
        CheckConstraint(UNIT_CHECK, name="ck_recipe_line_unit"),
    )
    recipe_line_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recipe_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("recipe_versions.recipe_version_id", ondelete="CASCADE"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SupplierIngredientTermModel(Base):
    __tablename__ = "supplier_ingredient_terms"
    __table_args__ = (
        UniqueConstraint("supplier_id", "ingredient_id", "version", name="uq_supplier_term_version"),
        CheckConstraint("unit_cost >= 0 AND moq >= 0 AND pack_size > 0 AND lead_time_days >= 0 AND (shelf_life_days IS NULL OR shelf_life_days >= 0)", name="ck_supplier_term_values"),
        CheckConstraint("unit_price_status IN ('declared', 'legacy_unknown')", name="ck_supplier_term_unit_price_status"),
        CheckConstraint("lead_time_status IN ('declared', 'legacy_unknown')", name="ck_supplier_term_lead_time_status"),
        CheckConstraint("version >= 1", name="ck_supplier_term_version"),
        CheckConstraint(UNIT_CHECK, name="ck_supplier_term_unit"),
        Index("ix_supplier_terms_store_ingredient", "store_id", "ingredient_id"),
    )
    constraint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), nullable=False)
    unit_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    moq: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    pack_size: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    order_unit: Mapped[str | None] = mapped_column(String(64))
    available_delivery_days: Mapped[str | None] = mapped_column(Text)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Explicit zero is valid.  The status keeps legacy silent-zero values from
    # being interpreted as authoritative by procurement planning.
    unit_price_status: Mapped[str] = mapped_column(String(32), nullable=False, default="declared")
    lead_time_status: Mapped[str] = mapped_column(String(32), nullable=False, default="declared")
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class LegacySupplierInventoryValueModel(Base):
    """Migration archive only; never used as an operational source of truth."""
    __tablename__ = "legacy_supplier_inventory_values"
    constraint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    capacity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))


class InventoryConstraintModel(Base):
    __tablename__ = "inventory_constraints"
    __table_args__ = (
        UniqueConstraint("store_id", "ingredient_id", "constraint_type", "version", name="uq_inventory_constraint_version"),
        CheckConstraint("value >= 0", name="ck_inventory_constraint_value"),
        CheckConstraint("version >= 1", name="ck_inventory_constraint_version"),
        CheckConstraint("end_date IS NULL OR end_date >= effective_date", name="ck_inventory_constraint_dates"),
        Index("ix_inventory_constraints_lookup", "store_id", "ingredient_id", "constraint_type", "effective_date"),
    )
    constraint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    ingredient_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"))
    constraint_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(3))
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))
    superseded_by_constraint_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("inventory_constraints.constraint_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class InventoryLotModel(Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint("source_import_id", "source_profile_id", "source_row_hash", name="uq_inventory_lot_provenance"),
        CheckConstraint("initial_quantity >= 0", name="ck_inventory_lot_quantity"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_inventory_lot_cost"),
        CheckConstraint("expiry_date IS NULL OR received_date IS NULL OR expiry_date >= received_date", name="ck_inventory_lot_dates"),
        CheckConstraint("received_date_status IN ('declared', 'unknown', 'legacy_unknown')", name="ck_inventory_lot_received_date_status"),
        CheckConstraint("received_date_status != 'declared' OR received_date IS NOT NULL", name="ck_inventory_lot_declared_received_date"),
        CheckConstraint("version >= 1", name="ck_inventory_lot_version"),
        CheckConstraint(UNIT_CHECK, name="ck_inventory_lot_unit"),
        Index("ix_inventory_lots_store_ingredient", "store_id", "ingredient_id"),
        Index("ix_inventory_lots_expiry_date", "expiry_date"),
        UniqueConstraint("store_id", "reconciliation_key", name="uq_inventory_lot_reconciliation"),
    )
    lot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"))
    batch_code: Mapped[str | None] = mapped_column(String(128))
    warehouse_name: Mapped[str | None] = mapped_column(String(255))
    received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_date_status: Mapped[str] = mapped_column(String(32), nullable=False, default="declared")
    expiry_date: Mapped[date | None] = mapped_column(Date)
    initial_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_cost: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reconciliation_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    last_counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryMovementModel(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        UniqueConstraint("source_import_id", "source_profile_id", "source_row_hash", name="uq_inventory_movement_provenance"),
        CheckConstraint("movement_type IN ('opening_balance','receipt','physical_count_adjustment','waste','expiry','manual_adjustment','usage','purchase_order_inbound')", name="ck_inventory_movement_type"),
        CheckConstraint(UNIT_CHECK, name="ck_inventory_movement_unit"),
        Index("ix_inventory_movements_lot_occurred", "lot_id", "occurred_at"),
    )
    movement_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    lot_id: Mapped[str] = mapped_column(String(36), ForeignKey("inventory_lots.lot_id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(128))
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SalesDailyModel(Base):
    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint("import_id", "profile_id", "source_row_hash", name="uq_sales_provenance"),
        CheckConstraint("quantity >= 0", name="ck_sales_quantity"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="ck_sales_price"),
        Index("ix_sales_store_date", "store_id", "date"), Index("ix_sales_product_date", "product_id", "date"),
        UniqueConstraint("store_id", "date", "product_id", "promotion", name="uq_sales_natural_key"),
    )
    sales_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.product_id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit_price: Mapped[int | None] = mapped_column(Integer)
    promotion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_stockout: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=utc_now, onupdate=utc_now)
    natural_key_hash: Mapped[str | None] = mapped_column(String(64))
    external_record_id: Mapped[str | None] = mapped_column(String(255))


class UsageDailyModel(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("import_id", "profile_id", "source_row_hash", name="uq_usage_provenance"),
        CheckConstraint("quantity >= 0", name="ck_usage_quantity"), CheckConstraint(UNIT_CHECK, name="ck_usage_unit"),
        Index("ix_usage_store_date", "store_id", "date"), Index("ix_usage_ingredient_date", "ingredient_id", "date"),
        UniqueConstraint("store_id", "date", "ingredient_id", name="uq_usage_natural_key"),
    )
    usage_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    usage_source: Mapped[str | None] = mapped_column(String(128))
    waste_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=utc_now, onupdate=utc_now)
    natural_key_hash: Mapped[str | None] = mapped_column(String(64))


class PurchaseReceiptModel(Base):
    __tablename__ = "purchase_receipts"
    __table_args__ = (
        UniqueConstraint("import_id", "profile_id", "source_row_hash", name="uq_receipt_provenance"),
        CheckConstraint("quantity >= 0", name="ck_receipt_quantity"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_receipt_cost"),
        CheckConstraint("expiry_date IS NULL OR expiry_date >= receipt_date", name="ck_receipt_dates"),
        CheckConstraint(UNIT_CHECK, name="ck_receipt_unit"),
        Index("ix_receipts_store_date", "store_id", "receipt_date"), Index("ix_receipts_ingredient_date", "ingredient_id", "receipt_date"),
        UniqueConstraint("store_id", "business_key_hash", name="uq_receipt_business_key"),
    )
    receipt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredients.ingredient_id"), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("suppliers.supplier_id"))
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_cost: Mapped[int | None] = mapped_column(Integer)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    purchase_order_id: Mapped[str | None] = mapped_column(String(128))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    batch_code: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    business_key_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    external_record_id: Mapped[str | None] = mapped_column(String(255))
    inventory_effect: Mapped[str] = mapped_column(String(32), nullable=False, default="record_only")
    po_id: Mapped[str | None] = mapped_column(String(36))
    po_line_id: Mapped[str | None] = mapped_column(String(36))


class CalendarFeatureModel(Base):
    __tablename__ = "calendar_features"
    __table_args__ = (UniqueConstraint("store_id", "date", name="uq_calendar_store_date"), Index("ix_calendar_date", "date"))
    calendar_feature_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(128), ForeignKey("stores.store_id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_holiday: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_store_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_promotion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    promotion_name: Mapped[str | None] = mapped_column(String(255))
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    rainfall: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_jobs.import_id"))
    source_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("import_sheet_profiles.profile_id"))
    source_row_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
