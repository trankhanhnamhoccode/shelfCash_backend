from fastapi import APIRouter, Depends, Header, Query

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.dependencies import get_menu_service, require_api_key
from app.schemas.menu import ComponentsReplace


router = APIRouter(tags=["menu"], dependencies=[Depends(require_api_key)])


@router.get("/stores/{store_id}/menu")
def get_menu(
    store_id: str,
    status: str = "active",
    item_type: str = "all",
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    service=Depends(get_menu_service),
):
    return service.menu(store_id, status, item_type, search, page, page_size)


@router.put("/stores/{store_id}/products/{product_id}/components")
def replace_components(
    store_id: str,
    product_id: str,
    body: ComponentsReplace,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service=Depends(get_menu_service),
):
    return service.replace_components(store_id, product_id, body, idempotency_key)


@router.get("/import-schemas")
def import_schemas():
    return {
        "schemas": {
            key: {
                "label": "Danh mục Menu" if key == "menu" else key.replace("_", " ").title(),
                "fields": value["fields"],
                "core_fields": value["core_fields"],
                "field_metadata": value.get("field_metadata", {}),
            }
            for key, value in CANONICAL_SCHEMAS.items()
            if key != "unknown"
        }
    }
