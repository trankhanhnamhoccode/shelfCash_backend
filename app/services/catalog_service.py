import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    DuplicateResourceError, ResourceNotFoundError, ValidationError, VersionConflictError,
)
from app.core.names import display_name, normalize_name
from app.core.provenance import canonical_hash
from app.core.units import convert_quantity, normalize_unit
from app.models.business import (
    IngredientAliasModel, IngredientModel, InventoryLotModel, ProductModel,
    PurchaseReceiptModel, RecipeLineModel, SupplierIngredientTermModel, UsageDailyModel,
)
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.business import CatalogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.recipes import RecipeRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.recipe_service import RecipeVersionService


class CatalogApiService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _sku(value):
        value = value.strip() if value else None
        return value or None

    @staticmethod
    def _ingredient_dict(model, aliases):
        return {
            "ingredient_id": model.ingredient_id, "store_id": model.store_id,
            "ingredient": model.ingredient, "sku": model.sku, "base_unit": model.base_unit,
            "aliases": aliases, "active": model.active, "version": model.version,
            "created_at": model.created_at, "updated_at": model.updated_at,
        }

    @staticmethod
    def _product_dict(model):
        return {
            "product_id": model.product_id, "store_id": model.store_id,
            "product": model.product, "sku": model.sku, "price": model.price,
            "active": model.active, "version": model.version,
            "created_at": model.created_at, "updated_at": model.updated_at,
        }

    def list_ingredients(self, store_id, active=None, q=None, sku=None):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            stmt = select(IngredientModel).where(IngredientModel.store_id == store_id)
            if active is not None: stmt = stmt.where(IngredientModel.active == active)
            if sku: stmt = stmt.where(IngredientModel.sku == self._sku(sku))
            if q: stmt = stmt.where(IngredientModel.normalized_name.contains(normalize_name(q)))
            models = list(session.scalars(stmt.order_by(IngredientModel.normalized_name, IngredientModel.ingredient_id)))
            aliases = list(session.scalars(select(IngredientAliasModel).where(IngredientAliasModel.store_id == store_id).order_by(IngredientAliasModel.normalized_alias)))
            by_id = {}
            for alias in aliases: by_id.setdefault(alias.ingredient_id, []).append(alias.alias)
            return [self._ingredient_dict(item, by_id.get(item.ingredient_id, [])) for item in models]

    def create_ingredient(self, store_id, data, key=None):
        body = data.model_dump()
        body["store_id"] = store_id
        return self._create_catalog(
            "POST", f"/api/v1/stores/{store_id}/ingredients", "ingredient", body, key
        )

    def _create_catalog(self, method, endpoint, kind, body, key):
        request_hash = canonical_hash(body)
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(body["store_id"])
                idem = IdempotencyService(IdempotencyRepository(session))
                if key:
                    replay = idem.register(store_id=body["store_id"], endpoint=endpoint, http_method=method, idempotency_key=key, request_hash=request_hash)
                    if replay.is_replay:
                        session.rollback()
                        return json.loads(replay.record.response_body_json)
                catalog = CatalogRepository(session)
                if kind == "ingredient":
                    model = catalog.add_ingredient(body["store_id"], body["ingredient"], normalize_unit(body["base_unit"]), sku=self._sku(body.get("sku")), source="manual")
                    model.active = body["active"]; model.version = 1
                    session.flush()
                    response = self._ingredient_dict(model, [])
                    action = "ingredient_created"
                else:
                    model = catalog.add_product(body["store_id"], body["product"], sku=self._sku(body.get("sku")), source="manual")
                    model.price, model.active, model.version = body.get("price"), body["active"], 1
                    session.flush()
                    response = self._product_dict(model)
                    action = "product_created"
                if key:
                    record = IdempotencyRepository(session).get(store_id=body["store_id"], endpoint=endpoint, http_method=method, idempotency_key=key)
                    record.resource_type, record.resource_id, record.response_status = kind, getattr(model, f"{kind}_id"), 201
                    record.response_body_json = json.dumps(response, default=str, ensure_ascii=False)
                AuditService(AuditLogRepository(session)).record(store_id=body["store_id"], action=action, resource_type=kind, resource_id=getattr(model, f"{kind}_id"), after={"version": 1}, source="catalog_api")
                session.commit()
                return response
            except IntegrityError:
                session.rollback()
                raise DuplicateResourceError(details={"resource": kind, "reason": "duplicate_name_or_sku"}) from None

    def patch_ingredient(self, store_id, ingredient_id, data):
        changes = data.model_dump(exclude_unset=True)
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(store_id)
                catalog = CatalogRepository(session)
                model = catalog.get_ingredient(store_id, ingredient_id)
                if model is None: raise ResourceNotFoundError(details={"resource": "ingredient"})
                if model.version != data.version:
                    raise VersionConflictError(details={"expected_version": data.version, "current_version": model.version})
                changed = []
                if "base_unit" in changes:
                    unit = normalize_unit(changes["base_unit"])
                    if unit != model.base_unit and self._has_dependencies(session, ingredient_id):
                        raise VersionConflictError("Không thể đổi base_unit vì ingredient đã có dữ liệu phụ thuộc.", {"reason": "base_unit_locked"})
                    model.base_unit = unit; changed.append("base_unit")
                if "ingredient" in changes:
                    model.ingredient = display_name(changes["ingredient"]); model.normalized_name = normalize_name(changes["ingredient"]); changed.append("ingredient")
                if "sku" in changes: model.sku = self._sku(changes["sku"]); changed.append("sku")
                if "active" in changes: model.active = changes["active"]; changed.append("active")
                model.version += 1; model.updated_at = datetime.now(timezone.utc)
                session.flush()
                aliases = list(session.scalars(select(IngredientAliasModel.alias).where(IngredientAliasModel.store_id == store_id, IngredientAliasModel.ingredient_id == ingredient_id).order_by(IngredientAliasModel.normalized_alias)))
                response = self._ingredient_dict(model, aliases)
                AuditService(AuditLogRepository(session)).record(store_id=store_id, action="ingredient_updated", resource_type="ingredient", resource_id=ingredient_id, after={"version": model.version, "changed_fields": changed}, source="catalog_api")
                session.commit(); return response
            except IntegrityError:
                session.rollback()
                raise DuplicateResourceError(details={"resource": "ingredient", "reason": "duplicate_name_or_sku"}) from None

    @staticmethod
    def _has_dependencies(session, ingredient_id):
        for model, field in (
            (RecipeLineModel, RecipeLineModel.ingredient_id), (SupplierIngredientTermModel, SupplierIngredientTermModel.ingredient_id),
            (InventoryLotModel, InventoryLotModel.ingredient_id), (UsageDailyModel, UsageDailyModel.ingredient_id),
            (PurchaseReceiptModel, PurchaseReceiptModel.ingredient_id),
        ):
            if session.scalar(select(func.count()).select_from(model).where(field == ingredient_id)):
                return True
        return False

    def list_aliases(self, store_id, ingredient_id=None):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            if ingredient_id and CatalogRepository(session).get_ingredient(store_id, ingredient_id) is None:
                raise ResourceNotFoundError(details={"resource": "ingredient"})
            stmt = select(IngredientAliasModel).where(IngredientAliasModel.store_id == store_id)
            if ingredient_id: stmt = stmt.where(IngredientAliasModel.ingredient_id == ingredient_id)
            return [{"alias_id": x.alias_id, "store_id": x.store_id, "ingredient_id": x.ingredient_id, "alias": x.alias, "created_at": x.created_at} for x in session.scalars(stmt.order_by(IngredientAliasModel.normalized_alias))]

    def create_alias(self, data, key=None):
        body, endpoint = data.model_dump(), "/api/v1/ingredient-aliases"
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(data.store_id)
                catalog = CatalogRepository(session)
                ingredient = catalog.get_ingredient(data.store_id, data.ingredient_id)
                if ingredient is None: raise ResourceNotFoundError(details={"resource": "ingredient"})
                if normalize_name(data.alias) == ingredient.normalized_name:
                    raise ValidationError("Alias trùng với tên ingredient.", {"reason": "redundant_alias"})
                idem = IdempotencyService(IdempotencyRepository(session))
                if key:
                    replay = idem.register(store_id=data.store_id, endpoint=endpoint, http_method="POST", idempotency_key=key, request_hash=canonical_hash(body))
                    if replay.is_replay:
                        session.rollback(); return json.loads(replay.record.response_body_json)
                model = catalog.add_alias(data.store_id, data.ingredient_id, data.alias)
                session.flush()
                response = {"alias_id": model.alias_id, "store_id": model.store_id, "ingredient_id": model.ingredient_id, "alias": model.alias, "created_at": model.created_at}
                if key:
                    record = IdempotencyRepository(session).get(store_id=data.store_id, endpoint=endpoint, http_method="POST", idempotency_key=key)
                    record.resource_type, record.resource_id, record.response_status, record.response_body_json = "ingredient_alias", model.alias_id, 201, json.dumps(response, default=str, ensure_ascii=False)
                AuditService(AuditLogRepository(session)).record(store_id=data.store_id, action="ingredient_alias_created", resource_type="ingredient_alias", resource_id=model.alias_id, after={"ingredient_id": data.ingredient_id}, source="catalog_api")
                session.commit(); return response
            except IntegrityError:
                session.rollback(); raise DuplicateResourceError(details={"resource": "ingredient_alias"}) from None

    def put_aliases(self, store_id, items, key=None):
        endpoint = f"/api/v1/stores/{store_id}/aliases"
        body = sorted(
            (item.model_dump() for item in items),
            key=lambda item: (normalize_name(item["source_name"]), item.get("ingredient_id") or "", normalize_name(item["canonical_name"])),
        )
        request_hash = canonical_hash({"store_id": store_id, "aliases": body})
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(store_id)
                idem = IdempotencyService(IdempotencyRepository(session))
                if key:
                    replay = idem.register(
                        store_id=store_id,
                        endpoint=endpoint,
                        http_method="PUT",
                        idempotency_key=key,
                        request_hash=request_hash,
                    )
                    if replay.is_replay:
                        session.rollback()
                        return json.loads(replay.record.response_body_json)

                catalog = CatalogRepository(session)
                results = []
                created_ids = []
                seen = set()
                for item in items:
                    normalized = normalize_name(item.source_name)
                    if normalized in seen:
                        raise ValidationError(
                            "Alias bị lặp trong request.",
                            {"alias": item.source_name, "reason": "duplicate_in_request"},
                        )
                    seen.add(normalized)
                    ingredient = catalog.get_ingredient(store_id, item.ingredient_id) if item.ingredient_id else session.scalar(
                        select(IngredientModel).where(
                            IngredientModel.store_id == store_id,
                            IngredientModel.normalized_name == normalize_name(item.canonical_name),
                        )
                    )
                    if ingredient is None:
                        raise ResourceNotFoundError(
                            details={"resource": "ingredient", "ingredient_id": item.ingredient_id, "canonical_name": item.canonical_name}
                        )
                    if normalize_name(item.canonical_name) != ingredient.normalized_name:
                        raise ValidationError(
                            "Alias trùng với tên ingredient.",
                            {"alias": item.source_name, "reason": "canonical_name_mismatch"},
                        )
                    existing = session.scalar(
                        select(IngredientAliasModel).where(
                            IngredientAliasModel.store_id == store_id,
                            IngredientAliasModel.normalized_alias == normalized,
                        )
                    )
                    if existing is not None:
                        if existing.ingredient_id != ingredient.ingredient_id:
                            existing.ingredient_id = ingredient.ingredient_id
                        model = existing
                    else:
                        model = catalog.add_alias(store_id, ingredient.ingredient_id, item.source_name)
                        session.flush()
                        created_ids.append(model.alias_id)
                    results.append(
                        {
                            "alias_id": model.alias_id,
                            "store_id": model.store_id,
                            "ingredient_id": model.ingredient_id,
                            "alias": model.alias,
                            "created_at": model.created_at,
                        }
                    )

                if key:
                    record = IdempotencyRepository(session).get(
                        store_id=store_id,
                        endpoint=endpoint,
                        http_method="PUT",
                        idempotency_key=key,
                    )
                    record.resource_type = "ingredient_aliases"
                    record.resource_id = ",".join(created_ids) or None
                    record.response_status = 200
                    record.response_body_json = json.dumps(
                        results, default=str, ensure_ascii=False
                    )
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id,
                    action="ingredient_aliases_upserted",
                    resource_type="ingredient_alias",
                    resource_id=created_ids[0] if len(created_ids) == 1 else store_id,
                    after={
                        "requested_count": len(items),
                        "created_count": len(created_ids),
                    },
                    source="catalog_api",
                )
                session.commit()
                return results
            except IntegrityError:
                session.rollback()
                raise DuplicateResourceError(
                    details={"resource": "ingredient_alias"}
                ) from None

    def list_products(self, store_id, active=None, q=None, sku=None):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            stmt = select(ProductModel).where(ProductModel.store_id == store_id)
            if active is not None: stmt = stmt.where(ProductModel.active == active)
            if sku: stmt = stmt.where(ProductModel.sku == self._sku(sku))
            if q: stmt = stmt.where(ProductModel.normalized_name.contains(normalize_name(q)))
            return [self._product_dict(x) for x in session.scalars(stmt.order_by(ProductModel.normalized_name, ProductModel.product_id))]

    def create_product(self, store_id, data, key=None):
        body = data.model_dump()
        body["store_id"] = store_id
        return self._create_catalog(
            "POST", f"/api/v1/stores/{store_id}/products", "product", body, key
        )

    def patch_product(self, store_id, product_id, data):
        changes = data.model_dump(exclude_unset=True)
        with self.session_factory() as session:
            try:
                StoreRepository(session).get_required(store_id)
                model = CatalogRepository(session).get_product(store_id, product_id)
                if model is None:
                    raise ResourceNotFoundError(details={"resource": "product"})
                if model.version != data.version:
                    raise VersionConflictError(
                        details={
                            "expected_version": data.version,
                            "current_version": model.version,
                        }
                    )
                changed = []
                if "product" in changes:
                    model.product = display_name(changes["product"])
                    model.normalized_name = normalize_name(changes["product"])
                    changed.append("product")
                if "sku" in changes:
                    model.sku = self._sku(changes["sku"])
                    changed.append("sku")
                if "price" in changes:
                    model.price = changes["price"]
                    changed.append("price")
                if "active" in changes:
                    model.active = changes["active"]
                    changed.append("active")
                model.version += 1
                model.updated_at = datetime.now(timezone.utc)
                session.flush()
                response = self._product_dict(model)
                AuditService(AuditLogRepository(session)).record(
                    store_id=store_id,
                    action="product_updated",
                    resource_type="product",
                    resource_id=product_id,
                    after={"version": model.version, "changed_fields": changed},
                    source="catalog_api",
                )
                session.commit()
                return response
            except IntegrityError:
                session.rollback()
                raise DuplicateResourceError(
                    details={"resource": "product", "reason": "duplicate_name_or_sku"}
                ) from None


class RecipeApiService:
    def __init__(self, session_factory): self.session_factory = session_factory

    def get(self, store_id, product_id, on_date=None):
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            catalog, repo = CatalogRepository(session), RecipeRepository(session)
            if catalog.get_product(store_id, product_id) is None: raise ResourceNotFoundError(details={"resource": "product"})
            if on_date:
                version = repo.get_active(store_id, product_id, on_date)
            else:
                versions = repo.get_versions(store_id, product_id)
                version = versions[-1] if versions else None
            return self._response(session, store_id, product_id, version)

    @staticmethod
    def _response(session, store_id, product_id, version):
        recipe = None
        if version is not None:
            lines = list(session.execute(select(RecipeLineModel, IngredientModel).join(IngredientModel, IngredientModel.ingredient_id == RecipeLineModel.ingredient_id).where(RecipeLineModel.recipe_version_id == version.recipe_version_id)))
            recipe = {"recipe_version_id": version.recipe_version_id, "version": version.version, "effective_from": version.effective_from, "effective_to": version.effective_to, "content_hash": version.content_hash, "lines": [{"recipe_line_id": line.recipe_line_id, "ingredient_id": ingredient.ingredient_id, "ingredient": ingredient.ingredient, "quantity": format(line.quantity.normalize(), "f"), "unit": line.unit} for line, ingredient in lines], "created_at": version.created_at}
        return {"product_id": product_id, "store_id": store_id, "recipe": recipe}

    def put(self, store_id, product_id, data, key=None):
        endpoint = f"/api/v1/stores/{store_id}/products/{product_id}/recipe"
        body = data.model_dump()
        body["store_id"] = store_id
        with self.session_factory() as session:
            StoreRepository(session).get_required(store_id)
            catalog, repo = CatalogRepository(session), RecipeRepository(session)
            if catalog.get_product(store_id, product_id) is None: raise ResourceNotFoundError(details={"resource": "product"})
            versions = repo.get_versions(store_id, product_id)
            current = versions[-1].version if versions else 0
            idem = IdempotencyService(IdempotencyRepository(session))
            if key:
                replay = idem.register(store_id=store_id, endpoint=endpoint, http_method="PUT", idempotency_key=key, request_hash=canonical_hash(body))
                if replay.is_replay:
                    session.rollback(); return json.loads(replay.record.response_body_json)
            if data.version != current:
                raise VersionConflictError(details={"expected_version": data.version, "current_version": current})
            converted = []
            for line in data.lines:
                ingredient = catalog.get_ingredient(store_id, line.ingredient_id)
                if ingredient is None: raise ResourceNotFoundError(details={"resource": "ingredient", "ingredient_id": line.ingredient_id})
                converted.append({"ingredient_id": line.ingredient_id, "quantity": convert_quantity(line.quantity, line.unit, ingredient.base_unit), "unit": ingredient.base_unit})
            before = len(versions)
            model = RecipeVersionService(repo).create_version(store_id, product_id, data.effective_from, converted, source="manual")
            session.flush()
            created = len(repo.get_versions(store_id, product_id)) > before
            response = self._response(session, store_id, product_id, model)
            if key:
                record = IdempotencyRepository(session).get(store_id=store_id, endpoint=endpoint, http_method="PUT", idempotency_key=key)
                record.resource_type, record.resource_id, record.response_status, record.response_body_json = "recipe_version", model.recipe_version_id, 201 if created else 200, json.dumps(response, default=str, ensure_ascii=False)
            AuditService(AuditLogRepository(session)).record(store_id=store_id, action="recipe_version_created" if created else "recipe_version_reused", resource_type="recipe_version", resource_id=model.recipe_version_id, after={"version": model.version}, source="recipe_api")
            session.commit(); return response
