from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.repositories.business import CatalogRepository


class EntityResolutionService:
    def __init__(self, repository: CatalogRepository):
        self.repository = repository

    def ingredient(self, store_id: str, *, ingredient_id=None, sku=None, name=None, base_unit=None, create_if_missing=False):
        found = self.repository.get_ingredient(store_id, ingredient_id) if ingredient_id else None
        found = found or (self.repository.get_ingredient_by_sku(store_id, sku) if sku else None)
        found = found or (self.repository.resolve_alias(store_id, name) if name else None)
        found = found or (self.repository.get_ingredient_by_name(store_id, name) if name else None)
        if found:
            return found
        if create_if_missing:
            if not name or not base_unit:
                raise ValidationError("Tên và base_unit bắt buộc khi tạo ingredient.")
            return self.repository.add_ingredient(store_id, name, base_unit, sku=sku, source="import")
        raise ResourceNotFoundError(details={"resource": "ingredient", "store_id": store_id})

    def product(self, store_id: str, *, product_id=None, sku=None, name=None, create_if_missing=False):
        found = self.repository.get_product(store_id, product_id) if product_id else None
        found = found or (self.repository.get_product_by_sku(store_id, sku) if sku else None)
        if not found and name and not sku:
            matches = self.repository.get_products_by_name(store_id, name)
            if len(matches) > 1:
                raise ValidationError(
                    "Tên product khớp nhiều biến thể; cần bổ sung SKU.",
                    {"code": "AMBIGUOUS_PRODUCT_VARIANT", "product": name},
                )
            found = matches[0] if matches else None
        if found: return found
        if create_if_missing and name: return self.repository.add_product(store_id, name, sku=sku, source="import")
        raise ResourceNotFoundError(details={"resource": "product", "store_id": store_id})

    def supplier(self, store_id: str, *, supplier_id=None, name=None, create_if_missing=False):
        found = self.repository.get_supplier(store_id, supplier_id) if supplier_id else None
        found = found or (self.repository.get_supplier_by_name(store_id, name) if name else None)
        if found: return found
        if create_if_missing and name: return self.repository.add_supplier(store_id, name, source="import")
        raise ResourceNotFoundError(details={"resource": "supplier", "store_id": store_id})
