from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.names import display_name, normalize_name
from app.core.units import normalize_unit
from app.models.business import (
    CalendarFeatureModel, IngredientAliasModel, IngredientModel, InventoryLotModel,
    InventoryMovementModel, ProductModel, PurchaseReceiptModel, RecipeLineModel,
    RecipeVersionModel, SalesDailyModel, StoreSettingsModel, SupplierIngredientTermModel,
    SupplierModel, UsageDailyModel,
)
from app.repositories.stores import StoreRepository


def _id() -> str:
    return str(uuid4())


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def _require_store(self, store_id: str) -> None:
        StoreRepository(self.session).get_required(store_id)

    def get_ingredient(self, store_id: str, ingredient_id: str) -> IngredientModel | None:
        return self.session.scalar(select(IngredientModel).where(IngredientModel.store_id == store_id, IngredientModel.ingredient_id == ingredient_id))

    def get_ingredient_by_sku(self, store_id: str, sku: str) -> IngredientModel | None:
        return self.session.scalar(select(IngredientModel).where(IngredientModel.store_id == store_id, IngredientModel.sku == sku))

    def get_ingredient_by_name(self, store_id: str, name: str) -> IngredientModel | None:
        return self.session.scalar(select(IngredientModel).where(IngredientModel.store_id == store_id, IngredientModel.normalized_name == normalize_name(name)))

    def resolve_alias(self, store_id: str, alias: str) -> IngredientModel | None:
        return self.session.scalar(select(IngredientModel).join(IngredientAliasModel).where(IngredientAliasModel.store_id == store_id, IngredientAliasModel.normalized_alias == normalize_name(alias), IngredientModel.store_id == store_id))

    def add_ingredient(self, store_id: str, name: str, base_unit: str, sku: str | None = None, source: str = "manual") -> IngredientModel:
        self._require_store(store_id)
        item = IngredientModel(ingredient_id=_id(), store_id=store_id, ingredient=display_name(name), normalized_name=normalize_name(name), sku=sku, base_unit=normalize_unit(base_unit), source=source)
        self.session.add(item)
        return item

    def get_or_create_ingredient(self, store_id: str, name: str, base_unit: str, source: str = "manual") -> IngredientModel:
        return self.get_ingredient_by_name(store_id, name) or self.add_ingredient(store_id, name, base_unit, source=source)

    def add_alias(self, store_id: str, ingredient_id: str, alias: str) -> IngredientAliasModel:
        ingredient = self.get_ingredient(store_id, ingredient_id)
        if ingredient is None:
            raise ResourceNotFoundError(details={"resource": "ingredient", "store_id": store_id})
        model = IngredientAliasModel(alias_id=_id(), store_id=store_id, ingredient_id=ingredient_id, alias=display_name(alias), normalized_alias=normalize_name(alias))
        self.session.add(model)
        return model

    def get_product(self, store_id: str, product_id: str) -> ProductModel | None:
        return self.session.scalar(select(ProductModel).where(ProductModel.store_id == store_id, ProductModel.product_id == product_id))

    def get_product_by_sku(self, store_id: str, sku: str) -> ProductModel | None:
        return self.session.scalar(select(ProductModel).where(ProductModel.store_id == store_id, ProductModel.sku == sku))

    def get_product_by_name(self, store_id: str, name: str) -> ProductModel | None:
        return self.session.scalar(select(ProductModel).where(ProductModel.store_id == store_id, ProductModel.normalized_name == normalize_name(name)))

    def add_product(self, store_id: str, name: str, sku: str | None = None, source: str = "manual") -> ProductModel:
        self._require_store(store_id)
        model = ProductModel(product_id=_id(), store_id=store_id, product=display_name(name), normalized_name=normalize_name(name), sku=sku, source=source)
        self.session.add(model)
        return model

    def get_or_create_product(self, store_id: str, name: str, source: str = "manual") -> ProductModel:
        return self.get_product_by_name(store_id, name) or self.add_product(store_id, name, source=source)

    def get_supplier(self, store_id: str, supplier_id: str) -> SupplierModel | None:
        return self.session.scalar(select(SupplierModel).where(SupplierModel.store_id == store_id, SupplierModel.supplier_id == supplier_id))

    def get_supplier_by_name(self, store_id: str, name: str) -> SupplierModel | None:
        return self.session.scalar(select(SupplierModel).where(SupplierModel.store_id == store_id, SupplierModel.normalized_name == normalize_name(name)))

    def add_supplier(self, store_id: str, name: str, source: str = "manual") -> SupplierModel:
        self._require_store(store_id)
        model = SupplierModel(supplier_id=_id(), store_id=store_id, supplier=display_name(name), normalized_name=normalize_name(name), source=source)
        self.session.add(model)
        return model

    def get_or_create_supplier(self, store_id: str, name: str, source: str = "manual") -> SupplierModel:
        return self.get_supplier_by_name(store_id, name) or self.add_supplier(store_id, name, source)


class SupplierTermRepository:
    def __init__(self, session: Session):
        self.session = session
        self.catalog = CatalogRepository(session)

    def add(self, term: SupplierIngredientTermModel) -> SupplierIngredientTermModel:
        supplier = self.catalog.get_supplier(term.store_id, term.supplier_id)
        ingredient = self.catalog.get_ingredient(term.store_id, term.ingredient_id)
        if supplier is None or ingredient is None:
            raise ValidationError("Supplier và ingredient phải thuộc cùng store.", {"store_id": term.store_id})
        term.unit = normalize_unit(term.unit)
        self.session.add(term)
        return term

    def get_active(self, store_id: str, ingredient_id: str) -> SupplierIngredientTermModel | None:
        return self.session.scalar(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id == store_id, SupplierIngredientTermModel.ingredient_id == ingredient_id, SupplierIngredientTermModel.active.is_(True)).order_by(SupplierIngredientTermModel.version.desc()))


class InventoryRepository:
    def __init__(self, session: Session):
        self.session = session
        self.catalog = CatalogRepository(session)

    def add_lot(self, lot: InventoryLotModel) -> InventoryLotModel:
        if self.catalog.get_ingredient(lot.store_id, lot.ingredient_id) is None:
            raise ValidationError("Lot và ingredient phải thuộc cùng store.")
        if lot.supplier_id and self.catalog.get_supplier(lot.store_id, lot.supplier_id) is None:
            raise ValidationError("Lot và supplier phải thuộc cùng store.")
        lot.unit = normalize_unit(lot.unit)
        self.session.add(lot)
        return lot

    def add_movement(self, movement: InventoryMovementModel) -> InventoryMovementModel:
        lot = self.session.scalar(select(InventoryLotModel).where(InventoryLotModel.store_id == movement.store_id, InventoryLotModel.lot_id == movement.lot_id))
        if lot is None:
            raise ValidationError("Movement và lot phải thuộc cùng store.")
        movement.unit = normalize_unit(movement.unit)
        self.session.add(movement)
        return movement

    def calculate_lot_balance(self, store_id: str, lot_id: str) -> Decimal:
        lot = self.session.scalar(select(InventoryLotModel).where(InventoryLotModel.store_id == store_id, InventoryLotModel.lot_id == lot_id))
        if lot is None:
            raise ResourceNotFoundError(details={"resource": "inventory_lot", "store_id": store_id})
        value = self.session.scalar(select(func.coalesce(func.sum(InventoryMovementModel.quantity_delta), 0)).where(InventoryMovementModel.store_id == store_id, InventoryMovementModel.lot_id == lot_id))
        return Decimal(value)

    def list_lots(self, store_id: str) -> list[InventoryLotModel]:
        return list(self.session.scalars(select(InventoryLotModel).where(InventoryLotModel.store_id == store_id)))


class HistoryRepository:
    _types = {"sales": SalesDailyModel, "usage": UsageDailyModel, "receipt": PurchaseReceiptModel}

    def __init__(self, session: Session):
        self.session = session
        self.catalog = CatalogRepository(session)

    def add_sales(self, model: SalesDailyModel) -> SalesDailyModel:
        if self.catalog.get_product(model.store_id, model.product_id) is None:
            raise ValidationError("Sales và product phải thuộc cùng store.")
        self.session.add(model); return model

    def add_usage(self, model: UsageDailyModel) -> UsageDailyModel:
        if self.catalog.get_ingredient(model.store_id, model.ingredient_id) is None:
            raise ValidationError("Usage và ingredient phải thuộc cùng store.")
        model.unit = normalize_unit(model.unit); self.session.add(model); return model

    def add_receipt(self, model: PurchaseReceiptModel) -> PurchaseReceiptModel:
        if self.catalog.get_ingredient(model.store_id, model.ingredient_id) is None or (model.supplier_id and self.catalog.get_supplier(model.store_id, model.supplier_id) is None):
            raise ValidationError("Receipt entities phải thuộc cùng store.")
        model.unit = normalize_unit(model.unit); self.session.add(model); return model

    def exists_by_provenance(self, kind: str, import_id: str, profile_id: str, row_hash: str) -> bool:
        model = self._types[kind]
        return self.session.scalar(select(model).where(model.import_id == import_id, model.profile_id == profile_id, model.source_row_hash == row_hash)) is not None

    def range(self, kind: str, store_id: str, start: date, end: date):
        model = self._types[kind]
        field = model.receipt_date if kind == "receipt" else model.date
        return list(self.session.scalars(select(model).where(model.store_id == store_id, field >= start, field <= end).order_by(field)))


class StoreConfigurationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_settings(self, store_id: str) -> StoreSettingsModel | None:
        return self.session.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id == store_id))

    def upsert_settings(self, settings: StoreSettingsModel) -> StoreSettingsModel:
        StoreRepository(self.session).get_required(settings.store_id)
        current = self.get_settings(settings.store_id)
        if current is None:
            self.session.add(settings); return settings
        for field in ("monthly_budget", "forecast_horizon", "default_strategy", "safety_policy_json"):
            setattr(current, field, getattr(settings, field))
        current.version += 1
        return current

    def get_calendar(self, store_id: str, day: date) -> CalendarFeatureModel | None:
        return self.session.scalar(select(CalendarFeatureModel).where(CalendarFeatureModel.store_id == store_id, CalendarFeatureModel.date == day))

    def upsert_calendar(self, feature: CalendarFeatureModel) -> CalendarFeatureModel:
        StoreRepository(self.session).get_required(feature.store_id)
        current = self.get_calendar(feature.store_id, feature.date)
        if current is None:
            self.session.add(feature); return feature
        for field in ("is_weekend", "is_holiday", "is_store_closed", "is_promotion", "promotion_name", "temperature", "rainfall", "source"):
            setattr(current, field, getattr(feature, field))
        return current
