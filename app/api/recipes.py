from datetime import date

from fastapi import APIRouter, Depends, Header

from app.dependencies import get_recipe_api_service, require_api_key
from app.schemas.recipes import ActiveRecipeResponse, RecipeWrite

router = APIRouter(tags=["recipes"], dependencies=[Depends(require_api_key)])


@router.get("/stores/{store_id}/products/{product_id}/recipe", response_model=ActiveRecipeResponse, summary="Get active recipe")
def get_recipe(store_id: str, product_id: str, on_date: date | None = None, service=Depends(get_recipe_api_service)):
    return service.get(store_id, product_id, on_date)


@router.put("/stores/{store_id}/products/{product_id}/recipe", response_model=ActiveRecipeResponse, summary="Create or reuse recipe version")
def put_recipe(store_id: str, product_id: str, body: RecipeWrite, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), service=Depends(get_recipe_api_service)):
    return service.put(store_id, product_id, body, idempotency_key)
