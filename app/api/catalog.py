from fastapi import APIRouter, Depends, Header

from app.dependencies import get_catalog_service, get_menu_service, require_api_key
from app.schemas.catalog import (
    AliasBulkUpsert, AliasResponse, IngredientCreate, IngredientPatch, IngredientResponse,
    ProductResponse,
)
from app.schemas.menu import MenuProductCreate, MenuProductPatch

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_api_key)])


@router.get("/stores/{store_id}/ingredients", response_model=list[IngredientResponse], summary="List ingredients")
def list_ingredients(store_id: str, active: bool | None = None, q: str | None = None, sku: str | None = None, service=Depends(get_catalog_service)):
    return service.list_ingredients(store_id, active, q, sku)


@router.post("/stores/{store_id}/ingredients", response_model=IngredientResponse, status_code=201, summary="Create ingredient")
def create_ingredient(store_id: str, body: IngredientCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), service=Depends(get_catalog_service)):
    return service.create_ingredient(store_id, body, idempotency_key)


@router.patch("/stores/{store_id}/ingredients/{ingredient_id}", response_model=IngredientResponse, summary="Update ingredient")
def patch_ingredient(store_id: str, ingredient_id: str, body: IngredientPatch, service=Depends(get_catalog_service)):
    return service.patch_ingredient(store_id, ingredient_id, body)


@router.get("/stores/{store_id}/aliases", response_model=list[AliasResponse], summary="List ingredient aliases")
def list_aliases(store_id: str, ingredient_id: str | None = None, service=Depends(get_catalog_service)):
    return service.list_aliases(store_id, ingredient_id)


@router.put("/stores/{store_id}/aliases", response_model=list[AliasResponse], summary="Bulk upsert ingredient aliases")
def put_aliases(
    store_id: str,
    body: AliasBulkUpsert,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service=Depends(get_catalog_service),
):
    return service.put_aliases(store_id, body.aliases, idempotency_key)


@router.get("/stores/{store_id}/products", summary="List products")
def list_products(store_id: str, active: bool | None = None, q: str | None = None, sku: str | None = None, service=Depends(get_menu_service)):
    return service.list_products(store_id, active, q, sku)


@router.post("/stores/{store_id}/products", status_code=201, summary="Create product")
def create_product(store_id: str, body: MenuProductCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), service=Depends(get_menu_service)):
    return service.create_product(store_id, body, idempotency_key)


@router.patch("/stores/{store_id}/products/{product_id}", summary="Update product")
def patch_product(store_id: str, product_id: str, body: MenuProductPatch, service=Depends(get_menu_service)):
    return service.patch_product(store_id, product_id, body)
