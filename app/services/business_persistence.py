from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.core.provenance import canonical_hash, purchase_business_key, source_row_hash
from app.core.units import convert_quantity, normalize_unit, validate_compatible
from app.models.business import (
    CalendarFeatureModel, InventoryLotModel, InventoryMovementModel, PurchaseReceiptModel,
    SalesDailyModel, StoreSettingsModel, SupplierIngredientTermModel, UsageDailyModel,
)
from app.models.import_normalized import ImportIssueModel
from app.repositories.business import CatalogRepository, InventoryRepository
from app.repositories.recipes import RecipeRepository
from app.repositories.stores import StoreRepository
from app.services.entity_resolution import EntityResolutionService
from app.services.recipe_service import RecipeVersionService

SCHEMA_VERSION = "20260728_0004"


@dataclass
class BusinessWriteSummary:
    ingredients_created: int = 0
    products_created: int = 0
    suppliers_created: int = 0
    recipe_versions_created: int = 0
    inventory_lots_created: int = 0
    inventory_movements_created: int = 0
    sales_records_created: int = 0
    sales_records_updated: int = 0
    usage_records_created: int = 0
    usage_records_updated: int = 0
    purchase_receipts_created: int = 0
    supplier_terms_created: int = 0
    calendar_features_created: int = 0
    calendar_features_updated: int = 0
    settings_created: int = 0
    settings_updated: int = 0
    rows_skipped: int = 0
    warnings: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class ImportBusinessPersistenceService:
    def __init__(self, session: Session):
        self.session = session
        self.catalog = CatalogRepository(session)
        self.resolve = EntityResolutionService(self.catalog)
        self.inventory = InventoryRepository(session)
        self.recipes = RecipeVersionService(RecipeRepository(session))
        self.summary = BusinessWriteSummary()

    def persist(self, *, job, sheets: list[dict[str, Any]]) -> dict[str, int]:
        StoreRepository(self.session).get_required(job.store_id)
        for sheet in sheets:
            kind = sheet["sheet_type"]
            if kind == "unknown":
                self.summary.rows_skipped += len(sheet["rows"])
                continue
            handler = getattr(self, f"_persist_{kind}", None)
            if handler is None:
                raise ValidationError("Loại sheet chưa được hỗ trợ cho business persistence.", {"sheet_type": kind})
            handler(job, sheet)
        self.session.flush()
        return self.summary.to_dict()

    @staticmethod
    def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise ValidationError("Giá trị số không hợp lệ.", {"field": field}) from None
        if number < 0 or (positive and number <= 0):
            raise ValidationError("Giá trị số ngoài phạm vi.", {"field": field})
        return number

    @staticmethod
    def _date(value: Any, field: str) -> date:
        try:
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise ValidationError("Ngày không hợp lệ.", {"field": field}) from None

    def _row_hash(self, job, sheet, row, index):
        return source_row_hash(
            store_id=job.store_id, import_id=job.import_id, profile_id=sheet["profile_id"],
            sheet_id=sheet["sheet_id"], source_row=int(row.get("_source_excel_row") or index + 1),
            sheet_type=sheet["sheet_type"], row={k: v for k, v in row.items() if not k.startswith("_source")},
        )

    def _ingredient(self, store_id: str, row: dict, unit_field: str = "unit"):
        name = row.get("ingredient_name") or row.get("ingredient")
        unit = row.get(unit_field)
        if not name:
            raise ValidationError("Thiếu ingredient.", {"field": "ingredient_name"})
        existing = self.catalog.get_ingredient_by_name(store_id, name) or self.catalog.resolve_alias(store_id, name)
        if existing:
            if unit:
                validate_compatible(unit, existing.base_unit)
            return existing
        if not unit:
            raise ValidationError("Ingredient mới phải có unit.", {"field": unit_field})
        item = self.resolve.ingredient(store_id, name=name, base_unit=normalize_unit(unit), create_if_missing=True)
        self.summary.ingredients_created += 1
        self.session.flush()
        return item

    def _product(self, store_id: str, row: dict):
        name = row.get("product_name") or row.get("product")
        if not name:
            raise ValidationError("Thiếu product.", {"field": "product_name"})
        existing = self.catalog.get_product_by_name(store_id, name)
        if existing:
            return existing
        item = self.resolve.product(store_id, name=name, create_if_missing=True)
        self.summary.products_created += 1
        self.session.flush()
        return item

    def _supplier(self, store_id: str, name: str | None):
        if not name:
            return None
        existing = self.catalog.get_supplier_by_name(store_id, name)
        if existing:
            return existing
        item = self.resolve.supplier(store_id, name=name, create_if_missing=True)
        self.summary.suppliers_created += 1
        self.session.flush()
        return item

    def _persist_inventory(self, job, sheet):
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row)
            target = convert_quantity(self._decimal(row.get("on_hand"), "on_hand"), row.get("unit"), ingredient.base_unit)
            supplier = self._supplier(job.store_id, row.get("supplier_name"))
            batch = row.get("batch_id") or row.get("batch_code")
            expiry = self._date(row["expiry_date"], "expiry_date") if row.get("expiry_date") else None
            received = self._date(row.get("snapshot_date") or job.forecast_date or job.created_at.date(), "snapshot_date")
            reconciliation = canonical_hash({"ingredient_id": ingredient.ingredient_id, "supplier_id": supplier.supplier_id if supplier else None, "expiry_date": expiry, "unit": ingredient.base_unit})
            statement = select(InventoryLotModel).where(InventoryLotModel.store_id == job.store_id, InventoryLotModel.ingredient_id == ingredient.ingredient_id)
            statement = statement.where(InventoryLotModel.batch_code == batch) if batch else statement.where(InventoryLotModel.reconciliation_key == reconciliation)
            lots = list(self.session.scalars(statement))
            if len(lots) > 1:
                raise ValidationError("Không thể xác định duy nhất inventory lot.", {"reason": "ambiguous_inventory_lot", "source_row": index + 1})
            row_hash = self._row_hash(job, sheet, row, index)
            if not lots:
                lot = InventoryLotModel(
                    lot_id=str(uuid4()), store_id=job.store_id, ingredient_id=ingredient.ingredient_id,
                    supplier_id=supplier.supplier_id if supplier else None, batch_code=batch,
                    received_date=received, expiry_date=expiry, initial_quantity=target,
                    unit=ingredient.base_unit, source="import", source_import_id=job.import_id,
                    source_profile_id=sheet["profile_id"], source_row_hash=row_hash,
                    reconciliation_key=None if batch else reconciliation, version=1,
                )
                self.inventory.add_lot(lot)
                self.session.flush()
                delta, movement_type = target, "opening_balance"
                self.summary.inventory_lots_created += 1
            else:
                lot = lots[0]
                delta = target - self.inventory.calculate_lot_balance(job.store_id, lot.lot_id)
                movement_type = "physical_count_adjustment"
            if delta:
                self.inventory.add_movement(InventoryMovementModel(
                    movement_id=str(uuid4()), store_id=job.store_id, lot_id=lot.lot_id,
                    movement_type=movement_type, quantity_delta=delta, unit=ingredient.base_unit,
                    occurred_at=datetime.now(timezone.utc), source="import",
                    source_import_id=job.import_id, source_profile_id=sheet["profile_id"],
                    source_row_hash=row_hash,
                ))
                self.summary.inventory_movements_created += 1
            else:
                self.summary.rows_skipped += 1

    def _persist_sales_history(self, job, sheet):
        grouped: dict[tuple, dict] = {}
        for index, row in enumerate(sheet["rows"]):
            product = self._product(job.store_id, row)
            day = self._date(row.get("date"), "date")
            promotion = bool(row.get("is_promotion") or row.get("promotion_name"))
            key = (day, product.product_id, promotion)
            quantity = self._decimal(row.get("quantity_sold"), "quantity_sold")
            price = row.get("selling_price")
            price = None if price is None else int(self._decimal(price, "selling_price"))
            if key in grouped and grouped[key]["price"] != price:
                raise ValidationError("Nhiều unit price khác nhau cho cùng sales aggregate.", {"date": day.isoformat()})
            grouped.setdefault(key, {"quantity": Decimal(0), "price": price, "hashes": []})
            grouped[key]["quantity"] += quantity
            grouped[key]["hashes"].append(self._row_hash(job, sheet, row, index))
        for (day, product_id, promotion), data in grouped.items():
            model = self.session.scalar(select(SalesDailyModel).where(SalesDailyModel.store_id == job.store_id, SalesDailyModel.date == day, SalesDailyModel.product_id == product_id, SalesDailyModel.promotion == promotion))
            row_hash = canonical_hash({"source_rows": sorted(data["hashes"])})
            if model is None:
                self.session.add(SalesDailyModel(sales_record_id=str(uuid4()), store_id=job.store_id, date=day, product_id=product_id, quantity=data["quantity"], unit_price=data["price"], promotion=promotion, source="import", import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=row_hash))
                self.summary.sales_records_created += 1
            else:
                model.quantity, model.unit_price, model.import_id, model.profile_id, model.source_row_hash = data["quantity"], data["price"], job.import_id, sheet["profile_id"], row_hash
                self.summary.sales_records_updated += 1

    def _persist_usage_history(self, job, sheet):
        grouped = defaultdict(Decimal)
        hashes = defaultdict(list)
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row)
            day = self._date(row.get("date"), "date")
            quantity = convert_quantity(self._decimal(row.get("quantity_used"), "quantity_used"), row.get("unit"), ingredient.base_unit)
            key = (day, ingredient.ingredient_id, ingredient.base_unit)
            grouped[key] += quantity
            hashes[key].append(self._row_hash(job, sheet, row, index))
        for (day, ingredient_id, unit), quantity in grouped.items():
            model = self.session.scalar(select(UsageDailyModel).where(UsageDailyModel.store_id == job.store_id, UsageDailyModel.date == day, UsageDailyModel.ingredient_id == ingredient_id))
            row_hash = canonical_hash({"source_rows": sorted(hashes[(day, ingredient_id, unit)])})
            if model is None:
                self.session.add(UsageDailyModel(usage_record_id=str(uuid4()), store_id=job.store_id, date=day, ingredient_id=ingredient_id, quantity=quantity, unit=unit, source="import", import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=row_hash))
                self.summary.usage_records_created += 1
            else:
                model.quantity, model.unit, model.import_id, model.profile_id, model.source_row_hash = quantity, unit, job.import_id, sheet["profile_id"], row_hash
                self.summary.usage_records_updated += 1

    def _persist_recipes(self, job, sheet):
        grouped = defaultdict(list)
        for row in sheet["rows"]:
            product = self._product(job.store_id, row)
            ingredient = self._ingredient(job.store_id, row, "ingredient_unit")
            quantity = convert_quantity(self._decimal(row.get("ingredient_quantity"), "ingredient_quantity", positive=True), row.get("ingredient_unit"), ingredient.base_unit)
            effective = self._date(row.get("effective_date") or job.forecast_date or job.created_at.date(), "effective_date")
            grouped[(product.product_id, effective)].append({"ingredient_id": ingredient.ingredient_id, "quantity": quantity, "unit": ingredient.base_unit})
        for (product_id, effective), lines in grouped.items():
            before = len(self.recipes.repository.get_versions(job.store_id, product_id))
            self.recipes.create_version(job.store_id, product_id, effective, lines, source="import")
            after = len(self.recipes.repository.get_versions(job.store_id, product_id))
            self.summary.recipe_versions_created += int(after > before)

    def _persist_purchase_history(self, job, sheet):
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row)
            supplier = self._supplier(job.store_id, row.get("supplier_name"))
            day = self._date(row.get("purchase_date"), "purchase_date")
            quantity = convert_quantity(self._decimal(row.get("quantity_received"), "quantity_received"), row.get("unit"), ingredient.base_unit)
            cost = row.get("unit_price")
            cost = None if cost is None else int(self._decimal(cost, "unit_price"))
            expiry = self._date(row["expiry_date"], "expiry_date") if row.get("expiry_date") else None
            batch = row.get("batch_id")
            key = purchase_business_key(store_id=job.store_id, receipt_date=day, ingredient_id=ingredient.ingredient_id, supplier_id=supplier.supplier_id if supplier else None, quantity=quantity, unit=ingredient.base_unit, unit_cost=cost, expiry_date=expiry, batch_code=batch)
            if self.session.scalar(select(PurchaseReceiptModel).where(PurchaseReceiptModel.store_id == job.store_id, PurchaseReceiptModel.business_key_hash == key)):
                self.summary.rows_skipped += 1
                continue
            self.session.add(PurchaseReceiptModel(receipt_id=str(uuid4()), store_id=job.store_id, ingredient_id=ingredient.ingredient_id, supplier_id=supplier.supplier_id if supplier else None, receipt_date=day, quantity=quantity, unit=ingredient.base_unit, unit_cost=cost, expiry_date=expiry, batch_code=batch, source="import", import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=self._row_hash(job, sheet, row, index), business_key_hash=key))
            self.summary.purchase_receipts_created += 1

    def _persist_supplier_constraints(self, job, sheet):
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row, "order_unit")
            supplier = self._supplier(job.store_id, row.get("supplier_name"))
            if supplier is None:
                raise ValidationError("Thiếu supplier_name.")
            unit = row.get("order_unit") or row.get("package_base_unit")
            source_unit = "None" if unit is None else str(unit)
            target_unit = "None" if ingredient.base_unit is None else str(ingredient.base_unit)
            moq = convert_quantity(self._decimal(row.get("minimum_order_quantity") or 0, "minimum_order_quantity"), source_unit, target_unit)
            pack = convert_quantity(self._decimal(row.get("package_size"), "package_size", positive=True), source_unit, target_unit)
            if "None" in {source_unit, target_unit}:
                self.summary.warnings += 1
                self.summary.rows_skipped += 1
                self.session.add(ImportIssueModel(
                    issue_id=str(uuid4()), import_id=job.import_id,
                    profile_id=sheet["profile_id"],
                    source_row=int(row.get("_source_excel_row") or index + 1),
                    severity="warning", code="UNIT_MISSING",
                    message="Supplier constraint was skipped because its unit is missing.",
                    details_json=json.dumps({
                        "source_unit": source_unit,
                        "target_unit": target_unit,
                        "fallback_unit": "None",
                        "quantity_conversion": "unchanged",
                    }, ensure_ascii=False),
                    issue_source="business_persistence",
                ))
                continue
            cost = int(self._decimal(row.get("unit_price") or 0, "unit_price"))
            lead = int(self._decimal(row.get("lead_time_days") or 0, "lead_time_days"))
            content = canonical_hash({"unit_cost": cost, "moq": moq, "pack_size": pack, "lead_time_days": lead, "unit": ingredient.base_unit})
            latest = self.session.scalar(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id == job.store_id, SupplierIngredientTermModel.supplier_id == supplier.supplier_id, SupplierIngredientTermModel.ingredient_id == ingredient.ingredient_id).order_by(SupplierIngredientTermModel.version.desc()))
            if latest and latest.source_row_hash == content:
                self.summary.rows_skipped += 1
                continue
            if latest:
                latest.active = False
            self.session.add(SupplierIngredientTermModel(constraint_id=str(uuid4()), store_id=job.store_id, supplier_id=supplier.supplier_id, ingredient_id=ingredient.ingredient_id, unit_cost=cost, moq=moq, pack_size=pack, lead_time_days=lead, safety_stock=Decimal(0), unit=ingredient.base_unit, version=(latest.version + 1 if latest else 1), active=True, source="import", source_import_id=job.import_id, source_profile_id=sheet["profile_id"], source_row_hash=content))
            self.summary.supplier_terms_created += 1

    def _persist_calendar_features(self, job, sheet):
        for row in sheet["rows"]:
            day = self._date(row.get("date"), "date")
            values = {"is_weekend": day.weekday() >= 5, "is_holiday": bool(row.get("is_holiday")), "is_store_closed": bool(row.get("is_store_closed")), "is_promotion": bool(row.get("is_promotion")), "promotion_name": row.get("promotion_name"), "temperature": row.get("temperature"), "rainfall": row.get("rainfall")}
            model = self.session.scalar(select(CalendarFeatureModel).where(CalendarFeatureModel.store_id == job.store_id, CalendarFeatureModel.date == day))
            if model is None:
                self.session.add(CalendarFeatureModel(calendar_feature_id=str(uuid4()), store_id=job.store_id, date=day, source="import", **values))
                self.summary.calendar_features_created += 1
            elif any(getattr(model, key) != value for key, value in values.items()):
                for key, value in values.items(): setattr(model, key, value)
                model.source = "import"
                self.summary.calendar_features_updated += 1
            else:
                self.summary.rows_skipped += 1

    def _persist_business_constraints(self, job, sheet):
        values = {}
        for row in sheet["rows"]:
            kind, value = row.get("constraint_type"), row.get("value")
            if kind in {"monthly_budget", "forecast_horizon"}:
                values[kind] = int(self._decimal(value, kind))
            elif kind == "default_strategy":
                if value not in {"economy", "balanced", "safe"}:
                    raise ValidationError("default_strategy không hợp lệ.")
                values[kind] = value
            else:
                self.summary.rows_skipped += 1
        if not values:
            return
        if "forecast_horizon" in values and not 1 <= values["forecast_horizon"] <= 90:
            raise ValidationError("forecast_horizon phải từ 1 đến 90.")
        model = self.session.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id == job.store_id))
        if model is None:
            self.session.add(StoreSettingsModel(setting_id=str(uuid4()), store_id=job.store_id, monthly_budget=values.get("monthly_budget", 0), forecast_horizon=values.get("forecast_horizon", 7), default_strategy=values.get("default_strategy", "balanced"), version=1))
            self.summary.settings_created += 1
        elif any(getattr(model, key) != value for key, value in values.items()):
            for key, value in values.items(): setattr(model, key, value)
            model.version += 1
            self.summary.settings_updated += 1
