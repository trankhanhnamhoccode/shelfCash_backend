from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessConstraintError, ValidationError
from app.core.business_constraints import constraint_definition, normalize_constraint_type, validate_and_normalize_business_constraint
from app.core.exceptions import MenuError
from app.core.menu import components_empty, parse_combo_components, parse_explicit_component
from app.core.names import display_name, normalize_lookup_name, normalize_name
from app.core.packaging_units import is_known_packaging_unit, normalize_packaging_unit
from app.core.provenance import canonical_hash, purchase_business_key, source_row_hash
from app.core.units import convert_quantity, normalize_unit, validate_compatible
from app.models.business import (
    CalendarFeatureModel, InventoryConstraintModel, InventoryLotModel, InventoryMovementModel, ProductBundleLineModel, ProductModel, PurchaseReceiptModel,
    SalesDailyModel, StoreSettingsModel, SupplierIngredientTermModel, UsageDailyModel,
)
from app.models.import_normalized import ImportIssueModel
from app.repositories.business import CatalogRepository, InventoryRepository
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.recipes import RecipeRepository
from app.repositories.stores import StoreRepository
from app.services.entity_resolution import EntityResolutionService
from app.services.recipe_service import RecipeVersionService
from app.services.audit_service import AuditService

SCHEMA_VERSION = "20260804_0018"

_DAY_ALIASES={"mon":0,"monday":0,"thu 2":0,"thu hai":0,"t2":0,"tue":1,"tuesday":1,"thu 3":1,"thu ba":1,"t3":1,
    "wed":2,"wednesday":2,"thu 4":2,"thu tu":2,"t4":2,"thu":3,"thursday":3,"thu 5":3,"thu nam":3,"t5":3,
    "fri":4,"friday":4,"thu 6":4,"thu sau":4,"t6":4,"sat":5,"saturday":5,"thu 7":5,"thu bay":5,"t7":5,
    "sun":6,"sunday":6,"chu nhat":6,"cn":6}

def normalize_delivery_days(value):
    if value is None or str(value).strip()=="":return None
    raw=value if isinstance(value,(list,tuple)) else str(value).replace(";",",").split(",")
    result=[]
    for item in raw:
        key=normalize_lookup_name(str(item))
        if key.isdigit() and 0<=int(key)<=6:day=int(key)
        else:
            day=_DAY_ALIASES.get(key)
            if day is None:raise ValidationError("available_delivery_days contains an unsupported day.",{"field":"available_delivery_days","raw_value":item})
        if day not in result:result.append(day)
    return sorted(result)


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
    inventory_constraints_created: int = 0
    calendar_features_created: int = 0
    calendar_features_updated: int = 0
    settings_created: int = 0
    settings_updated: int = 0
    rows_skipped: int = 0
    warnings: int = 0
    menu_products_created: int = 0
    menu_products_updated: int = 0
    menu_bundle_lines_created: int = 0

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
        self.menu_result: list[dict[str, Any]] = []

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
        sku = str(row.get("product_sku") or "").strip().upper() or None
        if sku:
            existing = self.catalog.get_product_by_sku(store_id, sku)
            if existing:
                if existing.normalized_name != normalize_name(name):
                    raise MenuError(
                        "SKU_CONFLICT", "SKU đã thuộc product khác.",
                        {"sku": sku, "existing_product": existing.product, "product": name},
                        http_status=409,
                    )
                return existing
            item = self.resolve.product(store_id, sku=sku, name=name, create_if_missing=True)
        else:
            matches = self.catalog.get_products_by_name(store_id, name)
            if len(matches) > 1:
                raise MenuError(
                    "MISSING_SKU_FOR_DUPLICATE_NAME",
                    "Tên product có nhiều biến thể; cần bổ sung SKU.",
                    {"product": name}, http_status=422,
                )
            if matches:
                return matches[0]
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
                    warehouse_name=row.get("warehouse_name"),
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
            stockout = row.get("is_stockout")
            stockout = None if stockout is None else bool(stockout)
            if key in grouped and grouped[key]["price"] != price:
                raise ValidationError("Nhiều unit price khác nhau cho cùng sales aggregate.", {"date": day.isoformat()})
            grouped.setdefault(key, {"quantity": Decimal(0), "price": price, "hashes": [], "stockouts": []})
            grouped[key]["quantity"] += quantity
            grouped[key]["stockouts"].append(stockout)
            grouped[key]["hashes"].append(self._row_hash(job, sheet, row, index))
        for (day, product_id, promotion), data in grouped.items():
            model = self.session.scalar(select(SalesDailyModel).where(SalesDailyModel.store_id == job.store_id, SalesDailyModel.date == day, SalesDailyModel.product_id == product_id, SalesDailyModel.promotion == promotion))
            row_hash = canonical_hash({"source_rows": sorted(data["hashes"])})
            if model is None:
                known_stockouts = [value for value in data["stockouts"] if value is not None]
                self.session.add(SalesDailyModel(sales_record_id=str(uuid4()), store_id=job.store_id, date=day, product_id=product_id, quantity=data["quantity"], unit_price=data["price"], promotion=promotion, is_stockout=(any(known_stockouts) if known_stockouts else None), source="import", import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=row_hash))
                self.summary.sales_records_created += 1
            else:
                known_stockouts = [value for value in data["stockouts"] if value is not None]
                model.quantity, model.unit_price, model.is_stockout, model.import_id, model.profile_id, model.source_row_hash = data["quantity"], data["price"], (any(known_stockouts) if known_stockouts else None), job.import_id, sheet["profile_id"], row_hash
                self.summary.sales_records_updated += 1

    def _persist_usage_history(self, job, sheet):
        grouped = defaultdict(Decimal)
        waste_grouped=defaultdict(Decimal);sources=defaultdict(set)
        hashes = defaultdict(list)
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row)
            day = self._date(row.get("date"), "date")
            quantity = convert_quantity(self._decimal(row.get("quantity_used"), "quantity_used"), row.get("unit"), ingredient.base_unit)
            key = (day, ingredient.ingredient_id, ingredient.base_unit)
            grouped[key] += quantity
            waste_raw=row.get("waste_quantity")
            if waste_raw not in (None,""):
                waste_grouped[key]+=convert_quantity(self._decimal(waste_raw,"waste_quantity"),row.get("unit"),ingredient.base_unit)
            if row.get("source"):sources[key].add(str(row.get("source")))
            hashes[key].append(self._row_hash(job, sheet, row, index))
        for (day, ingredient_id, unit), quantity in grouped.items():
            model = self.session.scalar(select(UsageDailyModel).where(UsageDailyModel.store_id == job.store_id, UsageDailyModel.date == day, UsageDailyModel.ingredient_id == ingredient_id))
            row_hash = canonical_hash({"source_rows": sorted(hashes[(day, ingredient_id, unit)])})
            if model is None:
                self.session.add(UsageDailyModel(usage_record_id=str(uuid4()), store_id=job.store_id, date=day, ingredient_id=ingredient_id, quantity=quantity, unit=unit, source="import",usage_source=",".join(sorted(sources[(day,ingredient_id,unit)])) or None,waste_quantity=waste_grouped[(day,ingredient_id,unit)], import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=row_hash))
                self.summary.usage_records_created += 1
            else:
                model.quantity, model.unit,model.usage_source,model.waste_quantity, model.import_id, model.profile_id, model.source_row_hash = quantity, unit,",".join(sorted(sources[(day,ingredient_id,unit)])) or None,waste_grouped[(day,ingredient_id,unit)], job.import_id, sheet["profile_id"], row_hash
                self.summary.usage_records_updated += 1

    def _persist_recipes(self, job, sheet):
        grouped = defaultdict(list);metadata={}
        for row in sheet["rows"]:
            product = self._product(job.store_id, row)
            ingredient = self._ingredient(job.store_id, row, "ingredient_unit")
            quantity = convert_quantity(self._decimal(row.get("ingredient_quantity"), "ingredient_quantity", positive=True), row.get("ingredient_unit"), ingredient.base_unit)
            effective = self._date(row.get("effective_date") or job.forecast_date or job.created_at.date(), "effective_date")
            grouped[(product.product_id, effective)].append({"ingredient_id": ingredient.ingredient_id, "quantity": quantity, "unit": ingredient.base_unit})
            key=(product.product_id,effective);candidate=(row.get("yield_quantity") or 1,row.get("yield_unit"),row.get("recipe_version"))
            if key in metadata and metadata[key]!=candidate:raise ValidationError("Recipe rows disagree on yield/version metadata.")
            metadata[key]=candidate
        for (product_id, effective), lines in grouped.items():
            before = len(self.recipes.repository.get_versions(job.store_id, product_id))
            yield_quantity,yield_unit,requested_version=metadata[(product_id,effective)]
            self.recipes.create_version(job.store_id, product_id, effective, lines, source="import",
                yield_quantity=self._decimal(yield_quantity,"yield_quantity",positive=True),yield_unit=yield_unit,requested_version=requested_version)
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
            total_cost=None if row.get("total_cost") in (None,"") else self._decimal(row.get("total_cost"),"total_cost")
            expiry = self._date(row["expiry_date"], "expiry_date") if row.get("expiry_date") else None
            batch = row.get("batch_id")
            key = purchase_business_key(store_id=job.store_id, receipt_date=day, ingredient_id=ingredient.ingredient_id, supplier_id=supplier.supplier_id if supplier else None, quantity=quantity, unit=ingredient.base_unit, unit_cost=cost, expiry_date=expiry, batch_code=batch)
            if self.session.scalar(select(PurchaseReceiptModel).where(PurchaseReceiptModel.store_id == job.store_id, PurchaseReceiptModel.business_key_hash == key)):
                self.summary.rows_skipped += 1
                continue
            self.session.add(PurchaseReceiptModel(receipt_id=str(uuid4()), store_id=job.store_id, ingredient_id=ingredient.ingredient_id, supplier_id=supplier.supplier_id if supplier else None, receipt_date=day, quantity=quantity, unit=ingredient.base_unit, unit_cost=cost,total_cost=total_cost,purchase_order_id=row.get("purchase_order_id"), expiry_date=expiry, batch_code=batch, source="import", import_id=job.import_id, profile_id=sheet["profile_id"], source_row_hash=self._row_hash(job, sheet, row, index), business_key_hash=key))
            self.summary.purchase_receipts_created += 1

    def _persist_supplier_constraints(self, job, sheet):
        for index, row in enumerate(sheet["rows"]):
            # order_unit is packaging metadata; it must never enter the
            # physical-unit compatibility/conversion helpers.
            ingredient = self._ingredient(
                job.store_id, row, "package_base_unit"
            )
            supplier = self._supplier(
                job.store_id, row.get("supplier_name")
            )
            if supplier is None:
                raise ValidationError(
                    "Thiếu supplier_name.",
                    {"field": "supplier_name"},
                )

            raw_order_unit = row.get("order_unit")
            order_unit = normalize_packaging_unit(raw_order_unit)
            minimum_packages = self._decimal(
                row.get("minimum_order_quantity"),
                "minimum_order_quantity",
                positive=True,
            )
            package_size = self._decimal(
                row.get("package_size"),
                "package_size",
                positive=True,
            )
            source_base_unit = normalize_unit(row.get("package_base_unit"))
            target_base_unit = normalize_unit(ingredient.base_unit)
            if "None" in {source_base_unit, target_base_unit}:
                raise ValidationError(
                    "Thiếu đơn vị vật lý của supplier constraint.",
                    {
                        "field": "package_base_unit",
                        "source_base_unit": source_base_unit,
                        "ingredient_base_unit": target_base_unit,
                    },
                )
            validate_compatible(source_base_unit, target_base_unit)
            package_size_in_base_unit = convert_quantity(
                package_size, source_base_unit, target_base_unit
            )
            minimum_base_quantity = (
                minimum_packages * package_size_in_base_unit
            )

            if not is_known_packaging_unit(raw_order_unit):
                self.summary.warnings += 1
                self.session.add(ImportIssueModel(
                    issue_id=str(uuid4()),
                    import_id=job.import_id,
                    profile_id=sheet["profile_id"],
                    source_row=int(
                        row.get("_source_excel_row") or index + 1
                    ),
                    severity="warning",
                    code="UNKNOWN_PACKAGING_UNIT",
                    message=(
                        "Đơn vị đóng gói chưa có alias; "
                        "giữ nguyên literal đã chuẩn hóa."
                    ),
                    details_json=json.dumps({
                        "sheet": row.get("_source_sheet"),
                        "row_number": int(
                            row.get("_source_excel_row") or index + 1
                        ),
                        "field": "order_unit",
                        "value": raw_order_unit,
                        "normalized_value": order_unit,
                    }, ensure_ascii=False),
                    issue_source="business_persistence",
                ))

            cost = int(self._decimal(
                row.get("unit_price") or 0, "unit_price"
            ))
            lead = int(self._decimal(
                row.get("lead_time_days") or 0, "lead_time_days"
            ))
            delivery_days=normalize_delivery_days(row.get("available_delivery_days"))
            content = canonical_hash({
                "unit_cost": cost,
                "moq": minimum_base_quantity,
                "pack_size": package_size_in_base_unit,
                "lead_time_days": lead,
                "unit": target_base_unit,
                "order_unit": order_unit,
                "available_delivery_days":delivery_days,
            })
            latest = self.session.scalar(
                select(SupplierIngredientTermModel).where(
                    SupplierIngredientTermModel.store_id == job.store_id,
                    SupplierIngredientTermModel.supplier_id
                    == supplier.supplier_id,
                    SupplierIngredientTermModel.ingredient_id
                    == ingredient.ingredient_id,
                ).order_by(SupplierIngredientTermModel.version.desc())
            )
            if latest and latest.source_row_hash == content:
                self.summary.rows_skipped += 1
                continue
            if latest:
                latest.active = False
            self.session.add(SupplierIngredientTermModel(
                constraint_id=str(uuid4()),
                store_id=job.store_id,
                supplier_id=supplier.supplier_id,
                ingredient_id=ingredient.ingredient_id,
                unit_cost=cost,
                moq=minimum_base_quantity,
                pack_size=package_size_in_base_unit,
                order_unit=order_unit,
                available_delivery_days=None if delivery_days is None else json.dumps(delivery_days),
                lead_time_days=lead,
                unit=target_base_unit,
                version=(latest.version + 1 if latest else 1),
                active=True,
                source="import",
                source_import_id=job.import_id,
                source_profile_id=sheet["profile_id"],
                source_row_hash=content,
            ))
            self.summary.supplier_terms_created += 1

    def _persist_supplier_constraints_legacy(self, job, sheet):
        for index, row in enumerate(sheet["rows"]):
            ingredient = self._ingredient(job.store_id, row, "package_base_unit")
            supplier = self._supplier(job.store_id, row.get("supplier_name"))
            if supplier is None:
                raise ValidationError("Thiếu supplier_name.")
            unit = row.get("package_base_unit")
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
            self.session.add(SupplierIngredientTermModel(constraint_id=str(uuid4()), store_id=job.store_id, supplier_id=supplier.supplier_id, ingredient_id=ingredient.ingredient_id, unit_cost=cost, moq=moq, pack_size=pack, lead_time_days=lead, unit=ingredient.base_unit, version=(latest.version + 1 if latest else 1), active=True, source="import", source_import_id=job.import_id, source_profile_id=sheet["profile_id"], source_row_hash=content))
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
        settings_values = {}
        for row in sheet["rows"]:
            kind = normalize_constraint_type(row.get("constraint_type"))
            value = row.get("value")
            if kind in {"monthly_budget", "forecast_horizon"}:
                settings_values[kind] = int(self._decimal(value, kind)); continue
            if kind == "default_strategy":
                if value not in {"economy", "balanced", "safe"}: raise ValidationError("default_strategy is invalid.")
                settings_values[kind] = value; continue
            kind, definition = constraint_definition(kind)
            ingredient = None
            name = row.get("ingredient_name") or row.get("ingredient")
            if definition.scope == "ingredient" or (definition.scope == "store_or_ingredient" and name):
                ingredient = self.catalog.get_ingredient_by_name(job.store_id, name) or self.catalog.resolve_alias(job.store_id, name)
                if ingredient is None:
                    raise BusinessConstraintError("INGREDIENT_NOT_FOUND", "Ingredient for business constraint was not found.", {"ingredient_name": name})
            normalized = validate_and_normalize_business_constraint(kind, value, row.get("unit") or row.get("currency"), ingredient)
            try:
                effective = self._date(row.get("effective_date"), "effective_date")
                end_date = self._date(row.get("end_date"), "end_date") if row.get("end_date") else None
                if end_date is not None and end_date < effective: raise ValueError("end before effective")
            except Exception as exc:
                raise BusinessConstraintError("BUSINESS_CONSTRAINT_EFFECTIVE_DATE_INVALID", "Business constraint effective date is invalid.", {"constraint_type": kind}) from exc
            numeric, unit = normalized.value, normalized.unit
            ingredient_id = ingredient.ingredient_id if ingredient else None
            currency=unit if definition.dimension=="currency" else None;note=row.get("note")
            content = canonical_hash({"ingredient_id": ingredient_id, "constraint_type": kind, "value": numeric,
                "unit": unit,"currency":currency,"note":note, "effective_date": effective, "end_date": end_date})
            latest = self.session.scalar(select(InventoryConstraintModel).where(
                InventoryConstraintModel.store_id == job.store_id, InventoryConstraintModel.ingredient_id == ingredient_id,
                InventoryConstraintModel.constraint_type == kind).order_by(InventoryConstraintModel.version.desc()))
            if latest and latest.source_row_hash == content:
                self.summary.rows_skipped += 1; continue
            if latest and effective <= latest.effective_date:
                raise BusinessConstraintError("BUSINESS_CONSTRAINT_EFFECTIVE_DATE_INVALID", "A new constraint version must start after the previous version.",
                    {"constraint_type": kind, "effective_date": str(effective), "previous_effective_date": str(latest.effective_date)})
            if latest and latest.active:
                latest.active = False
                if latest.end_date is None or latest.end_date >= effective: latest.end_date = date.fromordinal(effective.toordinal() - 1)
            self.session.add(InventoryConstraintModel(constraint_id=str(uuid4()), store_id=job.store_id,
                ingredient_id=ingredient_id, constraint_type=kind, value=numeric, unit=unit, effective_date=effective,
                currency=currency,note=note,
                end_date=end_date, version=(latest.version + 1 if latest else 1), active=True, source="import",
                source_import_id=job.import_id, source_profile_id=sheet["profile_id"], source_row_hash=content))
            self.summary.inventory_constraints_created += 1
        if not settings_values: return
        if "forecast_horizon" in settings_values and not 1 <= settings_values["forecast_horizon"] <= 90:
            raise ValidationError("forecast_horizon must be between 1 and 90.")
        model = self.session.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id == job.store_id))
        if model is None:
            self.session.add(StoreSettingsModel(setting_id=str(uuid4()), store_id=job.store_id,
                monthly_budget=settings_values.get("monthly_budget", 0), forecast_horizon=settings_values.get("forecast_horizon", 7),
                default_strategy=settings_values.get("default_strategy", "balanced"), version=1)); self.summary.settings_created += 1
        elif any(getattr(model, key) != value for key, value in settings_values.items()):
            for key, value in settings_values.items(): setattr(model, key, value)
            model.version += 1; self.summary.settings_updated += 1

    def _persist_menu(self, job, sheet):
        rows = sheet["rows"]
        seen_skus: dict[str, dict] = {}
        prepared = []
        for index, row in enumerate(rows):
            sku = str(row.get("product_sku") or "").strip().upper()
            name = display_name(row.get("product_name"))
            normalized_name = normalize_name(name)
            item_type = row.get("item_type")
            status = row.get("status") or "active"
            unit = row.get("selling_unit")
            price = self._decimal(row.get("selling_price"), "selling_price", positive=True)
            if not sku:
                raise MenuError("CORE_FIELDS_MISSING", "Thiếu product_sku.", {"row": index + 1})
            explicit = parse_explicit_component(row)
            if item_type == "single" and (not components_empty(row.get("combo_components")) or explicit):
                raise MenuError("COMBO_COMPONENT_PARSE_ERROR", "Single product không có components.", {"sku": sku})
            parsed = ([explicit] if explicit else parse_combo_components(row.get("combo_components"))) if item_type == "combo" else []
            if item_type == "combo" and unit != "combo":
                raise MenuError("INVALID_PRODUCT_UNIT", "Combo phải dùng selling_unit=combo.", {"sku": sku})
            item = {
                "row": row, "sku": sku, "name": name, "normalized_name": normalized_name,
                "item_type": item_type, "status": status, "unit": unit,
                "price": int(price), "components": parsed, "index": index,
            }
            previous = seen_skus.get(sku)
            if previous:
                comparable = ("name", "item_type", "status", "unit", "price")
                if any(previous[key] != item[key] for key in comparable):
                    raise MenuError("SKU_CONFLICT", "SKU bị trùng với thông tin product xung đột.", {"sku": sku}, http_status=409)
                if item["components"] == previous["components"]:
                    self.summary.rows_skipped += 1
                elif explicit:
                    previous["components"].extend(item["components"])
                else:
                    raise MenuError("SKU_CONFLICT", "Duplicate SKU has conflicting components.", {"sku": sku}, http_status=409)
                continue
            seen_skus[sku] = item
            prepared.append(item)

        products_by_sku = {
            product.sku: product for product in self.session.scalars(
                select(ProductModel).where(ProductModel.store_id == job.store_id)
            ) if product.sku
        }
        existing_products = list(self.session.scalars(select(ProductModel).where(ProductModel.store_id == job.store_id)))
        incoming_identity_counts = defaultdict(set)
        for item in prepared:
            incoming_identity_counts[(item["normalized_name"], item["item_type"])].add(item["sku"])

        def upsert(item):
            product = products_by_sku.get(item["sku"])
            row_hash = self._row_hash(job, sheet, item["row"], item["index"])
            if product is None:
                legacy = [p for p in existing_products if p.sku is None and p.normalized_name == item["normalized_name"] and p.item_type == item["item_type"]]
                named_skus = [p for p in existing_products if p.sku and p.normalized_name == item["normalized_name"]]
                safe_single_identity = len(incoming_identity_counts[(item["normalized_name"], item["item_type"])]) == 1
                if len(legacy) == 1 and not named_skus and safe_single_identity:
                    product = legacy[0]
                    product.sku = item["sku"]
                    products_by_sku[item["sku"]] = product
                    AuditService(AuditLogRepository(self.session)).record(
                        store_id=job.store_id, action="legacy_product_sku_upgraded", resource_type="product",
                        resource_id=product.product_id, before={"sku": None, "product": product.product},
                        after={"sku": item["sku"], "product": item["name"], "import_id": job.import_id}, source="import",
                    )
            if product:
                if product.normalized_name != item["normalized_name"]:
                    raise MenuError("SKU_CONFLICT", "SKU đã thuộc product khác.", {"sku": item["sku"], "existing_product": product.product, "product": item["name"]}, http_status=409)
                if product.item_type != item["item_type"]:
                    raise MenuError("PRODUCT_TYPE_IMMUTABLE", "Không thể đổi item_type của SKU hiện có.", {"sku": item["sku"]}, http_status=409)
                changed = any([
                    product.product != item["name"], product.price != item["price"],
                    product.active != (item["status"] == "active"),
                    product.selling_unit != item["unit"], product.source_row_hash != row_hash,
                ])
                product.product = item["name"]; product.normalized_name = item["normalized_name"]
                product.price = item["price"]; product.active = item["status"] == "active"
                product.selling_unit = item["unit"]; product.source = "import"
                product.source_import_id = job.import_id; product.source_row_hash = row_hash
                if changed:
                    product.version += 1; self.summary.menu_products_updated += 1
            else:
                product = ProductModel(
                    product_id=str(uuid4()), store_id=job.store_id, sku=item["sku"],
                    product=item["name"], normalized_name=item["normalized_name"],
                    item_type=item["item_type"], selling_unit=item["unit"], price=item["price"],
                    active=item["status"] == "active", source="import", version=1,
                    source_import_id=job.import_id, source_row_hash=row_hash,
                )
                self.session.add(product); products_by_sku[item["sku"]] = product
                existing_products.append(product)
                self.summary.menu_products_created += 1
            return product

        # Phase 1: materialize all singles, variants, and combo headers.
        products_for_items = {item["sku"]: upsert(item) for item in prepared}
        self.session.flush()
        all_products = list(self.session.scalars(select(ProductModel).where(ProductModel.store_id == job.store_id)))
        products_by_id = {p.product_id: p for p in all_products}
        products_by_sku = {p.sku.upper(): p for p in all_products if p.sku}
        exact_names, base_names = defaultdict(list), defaultdict(list)
        for product in all_products:
            if product.active and product.item_type == "single":
                exact_names[normalize_lookup_name(product.product)].append(product)
                base_names[normalize_lookup_name(product.product, strip_variant=True)].append(product)

        def resolve_component(parsed):
            label = parsed.product_name or parsed.sku or parsed.product_id
            if parsed.product_id:
                candidate = products_by_id.get(parsed.product_id)
                candidates = [candidate] if candidate and candidate.active and candidate.item_type == "single" else []
            elif parsed.sku:
                candidate = products_by_sku.get(parsed.sku.upper())
                candidates = [candidate] if candidate and candidate.active and candidate.item_type == "single" else []
            else:
                exact_key = normalize_lookup_name(parsed.product_name)
                base_key = normalize_lookup_name(parsed.product_name, strip_variant=True)
                # A base-name-only reference must see every variant, including a
                # legacy exact-name row. A name carrying size/variant metadata
                # gets the more specific exact-name lookup first.
                candidates = base_names.get(base_key, []) if exact_key == base_key else exact_names.get(exact_key, [])
                if not candidates:
                    candidates = base_names.get(base_key, [])
            if not candidates:
                code = "COMPONENT_NOT_FOUND" if parsed.product_id or parsed.sku else "COMBO_COMPONENT_NOT_FOUND"
                raise MenuError(code, "Không tìm thấy component.", {"component": label})
            if len(candidates) > 1:
                raise MenuError(
                    "AMBIGUOUS_PRODUCT_VARIANT", "Component khớp nhiều SKU; cần chỉ rõ biến thể.",
                    {"component": label, "candidate_count": len(candidates), "candidates": [
                        {"product_id": p.product_id, "sku": p.sku, "product": p.product} for p in candidates
                    ]}, http_status=422,
                )
            return candidates[0]

        # Phase 2: resolve relationships against DB and the complete import batch.
        for item in prepared:
            if item["item_type"] != "combo":
                continue
            combo = products_for_items[item["sku"]]
            resolved = []
            names = set()
            for parsed in item["components"]:
                if (parsed.product_id == combo.product_id or parsed.sku == combo.sku or
                        (parsed.normalized_name and parsed.normalized_name == item["normalized_name"])):
                    raise MenuError("COMBO_SELF_REFERENCE", "Combo không thể chứa chính nó.", {"sku": item["sku"]})
                component = resolve_component(parsed)
                if component.product_id in names:
                    raise MenuError("COMBO_COMPONENT_DUPLICATE", "Component combo bị trùng.")
                names.add(component.product_id); resolved.append((parsed, component))
            self.session.execute(delete(ProductBundleLineModel).where(
                ProductBundleLineModel.store_id == job.store_id,
                ProductBundleLineModel.combo_product_id == combo.product_id,
            ))
            for position, (parsed, component) in enumerate(resolved):
                self.session.add(ProductBundleLineModel(
                    bundle_line_id=str(uuid4()), store_id=job.store_id,
                    combo_product_id=combo.product_id,
                    component_product_id=component.product_id,
                    quantity=parsed.quantity, position=position,
                ))
                self.summary.menu_bundle_lines_created += 1
            calculated = sum(parsed.quantity * (component.price or 0) for parsed, component in resolved)
            if calculated <= 0:
                raise MenuError("INVALID_PRICE", "Component combo phải có giá hợp lệ.", {"sku": item["sku"]})
            supplied_list = item["row"].get("list_price")
            supplied_savings = item["row"].get("savings_amount")
            supplied_discount = item["row"].get("discount_rate")
            savings = max(calculated - combo.price, 0)
            discount = Decimal(savings) / Decimal(calculated)
            mismatch = (
                (supplied_list is not None and Decimal(str(supplied_list)) != Decimal(calculated))
                or (supplied_savings is not None and Decimal(str(supplied_savings)) != Decimal(savings))
                or (supplied_discount is not None and abs(Decimal(str(supplied_discount)) - discount) > Decimal("0.005"))
            )
            if mismatch:
                self.summary.warnings += 1
                self.session.add(ImportIssueModel(
                    issue_id=str(uuid4()), import_id=job.import_id, profile_id=sheet["profile_id"],
                    source_row=int(item["row"].get("_source_excel_row") or item["index"] + 1),
                    severity="warning", code="MENU_DERIVED_PRICE_MISMATCH",
                    message="Derived Menu price fields were recalculated.",
                    issue_source="business_persistence",
                ))
        self.session.flush()
        imported = list(self.session.scalars(select(ProductModel).where(
            ProductModel.store_id == job.store_id,
            ProductModel.source_import_id == job.import_id,
        ).order_by(ProductModel.normalized_name)))
        from app.services.menu_service import MenuService
        serializer = MenuService(None)
        graph = serializer._load_graph(self.session, job.store_id, imported)
        self.menu_result = [serializer.serialize(product, graph) for product in imported]
