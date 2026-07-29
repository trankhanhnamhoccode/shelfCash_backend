import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    DuplicateRequestError, MenuError, ResourceNotFoundError, VersionConflictError,
)
from app.core.menu import normalize_item_type, normalize_menu_status, normalize_product_unit
from app.core.names import display_name, normalize_name
from app.core.provenance import canonical_hash
from app.models.business import ProductBundleLineModel, ProductModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService


class MenuService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _status(product):
        return "active" if product.active else "inactive"

    @staticmethod
    def _normalize_sku(value):
        return str(value).strip().upper() if value is not None else None

    def _load_graph(self, session, store_id, products):
        ids = [product.product_id for product in products]
        lines = [] if not ids else list(session.scalars(
            select(ProductBundleLineModel).where(
                ProductBundleLineModel.store_id == store_id,
                ProductBundleLineModel.combo_product_id.in_(ids),
            ).order_by(
                ProductBundleLineModel.combo_product_id,
                ProductBundleLineModel.position,
            )
        ))
        component_ids = {line.component_product_id for line in lines}
        components = {} if not component_ids else {
            product.product_id: product for product in session.scalars(
                select(ProductModel).where(
                    ProductModel.store_id == store_id,
                    ProductModel.product_id.in_(component_ids),
                )
            )
        }
        grouped = {}
        for line in lines:
            grouped.setdefault(line.combo_product_id, []).append((line, components[line.component_product_id]))
        return grouped

    def serialize(self, product, graph):
        components = [
            {
                "bundle_line_id": line.bundle_line_id,
                "component_product_id": component.product_id,
                "sku": component.sku,
                "product": component.product,
                "quantity": line.quantity,
                "position": line.position,
                "price": component.price,
                "status": self._status(component),
            }
            for line, component in graph.get(product.product_id, [])
        ]
        if product.item_type == "combo":
            list_price = sum(
                item["quantity"] * (item["price"] or 0) for item in components
            )
            savings = max(list_price - (product.price or 0), 0)
            discount = float(Decimal(savings) / Decimal(list_price)) if list_price else 0.0
        else:
            list_price = product.price
            savings = 0
            discount = 0.0
        return {
            "product_id": product.product_id, "store_id": product.store_id,
            "sku": product.sku, "product": product.product,
            "item_type": product.item_type, "selling_unit": product.selling_unit,
            "price": product.price, "active": product.active,
            "status": self._status(product), "list_price": list_price,
            "discount_rate": round(discount, 6), "savings_amount": savings,
            "currency": "VND", "components": components,
            "version": product.version, "created_at": product.created_at,
            "updated_at": product.updated_at,
        }

    def _serialize_one(self, session, store_id, product):
        return self.serialize(product, self._load_graph(session, store_id, [product]))

    def _validate_components(self, session, store_id, combo, items):
        if not 1 <= len(items) <= 20:
            raise MenuError("COMBO_COMPONENTS_REQUIRED", "Combo phải có từ 1 đến 20 components.")
        ids = [item.component_product_id for item in items]
        if len(ids) != len(set(ids)):
            raise MenuError("COMBO_COMPONENT_DUPLICATE", "Component combo bị trùng.")
        if combo.product_id in ids:
            raise MenuError("COMBO_SELF_REFERENCE", "Combo không thể chứa chính nó.")
        products = {
            item.product_id: item for item in session.scalars(select(ProductModel).where(
                ProductModel.store_id == store_id, ProductModel.product_id.in_(ids)
            ))
        }
        missing = [item for item in ids if item not in products]
        if missing:
            raise ResourceNotFoundError(details={"resource": "component_product", "ids": missing})
        for component in products.values():
            if component.item_type != "single":
                raise MenuError("COMBO_NESTING_NOT_SUPPORTED", "Combo không thể chứa combo.")
            if combo.active and not component.active:
                raise MenuError("INACTIVE_COMBO_COMPONENT", "Combo active không thể chứa component inactive.")
        return products

    def _replace_components(self, session, store_id, combo, items):
        self._validate_components(session, store_id, combo, items)
        session.execute(delete(ProductBundleLineModel).where(
            ProductBundleLineModel.store_id == store_id,
            ProductBundleLineModel.combo_product_id == combo.product_id,
        ))
        for position, item in enumerate(items):
            session.add(ProductBundleLineModel(
                bundle_line_id=str(uuid4()), store_id=store_id,
                combo_product_id=combo.product_id,
                component_product_id=item.component_product_id,
                quantity=item.quantity, position=position,
            ))

    def list_products(self, store_id, active=None, q=None, sku=None):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            statement = select(ProductModel).where(ProductModel.store_id == store_id)
            if active is not None:
                statement = statement.where(ProductModel.active == active)
            if sku:
                statement = statement.where(ProductModel.sku == self._normalize_sku(sku))
            if q:
                statement = statement.where(ProductModel.normalized_name.contains(normalize_name(q)))
            products = list(session.scalars(statement.order_by(ProductModel.normalized_name)))
            graph = self._load_graph(session, store_id, products)
            return [self.serialize(product, graph) for product in products]

    def menu(self, store_id, status, item_type, search, page, page_size):
        if status not in {"active", "inactive", "all"}:
            raise MenuError("INVALID_MENU_STATUS", "Trạng thái Menu không hợp lệ.")
        if item_type not in {"single", "combo", "all"}:
            raise MenuError("INVALID_MENU_ITEM_TYPE", "Loại Menu không hợp lệ.")
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            summary_rows = list(session.scalars(select(ProductModel).where(ProductModel.store_id == store_id)))
            summary = {
                "single_count": sum(item.item_type == "single" for item in summary_rows),
                "combo_count": sum(item.item_type == "combo" for item in summary_rows),
                "active_count": sum(item.active for item in summary_rows),
                "inactive_count": sum(not item.active for item in summary_rows),
            }
            statement = select(ProductModel).where(ProductModel.store_id == store_id)
            if status != "all":
                statement = statement.where(ProductModel.active == (status == "active"))
            if item_type != "all":
                statement = statement.where(ProductModel.item_type == item_type)
            if search:
                term = normalize_name(search)
                statement = statement.where(or_(
                    ProductModel.normalized_name.contains(term),
                    func.lower(ProductModel.sku).contains(str(search).strip().casefold()),
                ))
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            products = list(session.scalars(statement.order_by(
                ProductModel.normalized_name, ProductModel.product_id
            ).offset((page - 1) * page_size).limit(page_size)))
            graph = self._load_graph(session, store_id, products)
            return {"items": [self.serialize(item, graph) for item in products],
                    "summary": summary, "page": page, "page_size": page_size, "total": total}

    def create_product(self, store_id, data, key=None):
        payload = data.model_dump(mode="json")
        endpoint = f"/api/v1/stores/{store_id}/products"
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(store_id)
                idem = IdempotencyService(IdempotencyRepository(session))
                if key:
                    replay = idem.register(store_id=store_id, endpoint=endpoint, http_method="POST",
                                           idempotency_key=key, request_hash=canonical_hash(payload))
                    if replay.is_replay:
                        session.rollback()
                        return json.loads(replay.record.response_body_json)
                item_type = normalize_item_type(data.item_type)
                status = normalize_menu_status(data.status) if data.status else ("active" if data.active is not False else "inactive")
                selling_unit = normalize_product_unit(data.selling_unit) if data.selling_unit else None
                if item_type == "combo" and selling_unit != "combo":
                    raise MenuError("INVALID_PRODUCT_UNIT", "Combo phải dùng selling_unit=combo.")
                if item_type == "combo" and not data.components:
                    raise MenuError("COMBO_COMPONENTS_REQUIRED", "Combo phải có components.")
                if item_type == "single" and data.components:
                    raise MenuError("COMBO_COMPONENT_PARSE_ERROR", "Single product không có components.")
                if data.price is not None and data.price <= 0:
                    raise MenuError("INVALID_PRICE", "Giá bán phải lớn hơn 0.")
                product = ProductModel(
                    product_id=str(uuid4()), store_id=store_id,
                    product=display_name(data.product), normalized_name=normalize_name(data.product),
                    sku=self._normalize_sku(data.sku), price=data.price, active=status == "active",
                    item_type=item_type, selling_unit=selling_unit, source="manual", version=1,
                )
                session.add(product); session.flush()
                if item_type == "combo":
                    self._replace_components(session, store_id, product, data.components)
                    session.flush()
                response = self._serialize_one(session, store_id, product)
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id, action="product_created", resource_type="product",
                    resource_id=product.product_id, after={"item_type": item_type, "version": 1},
                    source="catalog_api",
                )
                if key:
                    record = IdempotencyRepository(session).get(
                        store_id=store_id, endpoint=endpoint, http_method="POST", idempotency_key=key)
                    record.resource_type = "product"; record.resource_id = product.product_id
                    record.response_status = 201; record.response_body_json = json.dumps(response, default=str, ensure_ascii=False)
                session.commit(); return response
            except IntegrityError as exc:
                session.rollback()
                raise MenuError("DUPLICATE_PRODUCT_SKU", "SKU hoặc tên product đã tồn tại.", http_status=409) from None

    def patch_product(self, store_id, product_id, data):
        changes = data.model_dump(exclude_unset=True)
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(store_id)
                product = session.scalar(select(ProductModel).where(
                    ProductModel.store_id == store_id, ProductModel.product_id == product_id))
                if not product:
                    raise ResourceNotFoundError(details={"resource": "product"})
                if product.version != data.version:
                    raise VersionConflictError(details={"expected_version": data.version, "current_version": product.version})
                if data.item_type is not None and normalize_item_type(data.item_type) != product.item_type:
                    raise MenuError("PRODUCT_TYPE_IMMUTABLE", "Không thể đổi item_type.", http_status=409)
                if "status" in changes or "active" in changes:
                    active = normalize_menu_status(data.status) == "active" if data.status else bool(data.active)
                    if not active and product.item_type == "single":
                        used = session.scalar(select(ProductBundleLineModel).join(
                            ProductModel, ProductModel.product_id == ProductBundleLineModel.combo_product_id
                        ).where(
                            ProductBundleLineModel.store_id == store_id,
                            ProductBundleLineModel.component_product_id == product_id,
                            ProductModel.active.is_(True),
                        ))
                        if used:
                            raise MenuError("PRODUCT_IN_ACTIVE_COMBO", "Product đang được active combo sử dụng.", http_status=409)
                    product.active = active
                if "product" in changes:
                    product.product = display_name(data.product); product.normalized_name = normalize_name(data.product)
                if "sku" in changes:
                    product.sku = self._normalize_sku(data.sku)
                if "price" in changes:
                    if data.price is not None and data.price <= 0:
                        raise MenuError("INVALID_PRICE", "Giá bán phải lớn hơn 0.")
                    product.price = data.price
                if "selling_unit" in changes:
                    unit = normalize_product_unit(data.selling_unit)
                    if product.item_type == "combo" and unit != "combo":
                        raise MenuError("INVALID_PRODUCT_UNIT", "Combo phải dùng selling_unit=combo.")
                    product.selling_unit = unit
                product.version += 1; product.updated_at = datetime.now(timezone.utc)
                session.flush(); response = self._serialize_one(session, store_id, product)
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id, action="product_updated", resource_type="product",
                    resource_id=product_id, after={"version": product.version}, source="catalog_api")
                session.commit(); return response
            except IntegrityError:
                session.rollback()
                raise MenuError("DUPLICATE_PRODUCT_NAME", "SKU hoặc tên product đã tồn tại.", http_status=409) from None

    def replace_components(self, store_id, product_id, data, key=None):
        payload = data.model_dump(mode="json")
        endpoint = f"/api/v1/stores/{store_id}/products/{product_id}/components"
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            product = session.scalar(select(ProductModel).where(
                ProductModel.store_id == store_id, ProductModel.product_id == product_id))
            if not product:
                raise ResourceNotFoundError(details={"resource": "product"})
            if product.item_type != "combo":
                raise MenuError("COMBO_COMPONENTS_REQUIRED", "Target product không phải combo.")
            idem = IdempotencyService(IdempotencyRepository(session))
            if key:
                replay = idem.register(store_id=store_id, endpoint=endpoint, http_method="PUT",
                                       idempotency_key=key, request_hash=canonical_hash(payload))
                if replay.is_replay:
                    session.rollback(); return json.loads(replay.record.response_body_json)
            if product.version != data.version:
                raise VersionConflictError(details={"expected_version": data.version, "current_version": product.version})
            self._replace_components(session, store_id, product, data.components)
            product.version += 1; product.updated_at = datetime.now(timezone.utc)
            session.flush(); response = self._serialize_one(session, store_id, product)
            AuditService(AuditLogRepository(session)).record(
                store_id=store_id, action="combo_components_replaced", resource_type="product",
                resource_id=product_id, after={"version": product.version, "component_count": len(data.components)},
                source="catalog_api")
            if key:
                record = IdempotencyRepository(session).get(
                    store_id=store_id, endpoint=endpoint, http_method="PUT", idempotency_key=key)
                record.resource_type = "product"; record.resource_id = product_id
                record.response_status = 200; record.response_body_json = json.dumps(response, default=str, ensure_ascii=False)
            session.commit(); return response
