from datetime import date

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_operational_service, require_api_key

router = APIRouter(tags=["operational"], dependencies=[Depends(require_api_key)])


def paging(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    return page, page_size


@router.get("/stores/{store_id}/imports")
def imports(store_id: str, p=Depends(paging), status: str | None = None, date_from: date | None = None, date_to: date | None = None, service=Depends(get_operational_service)):
    return service.imports(store_id, *p, status, date_from, date_to)


@router.get("/stores/{store_id}/products/{product_id}/recipe-versions")
def recipe_versions(store_id: str, product_id: str, p=Depends(paging), service=Depends(get_operational_service)):
    return service.recipe_versions(store_id, product_id, *p)


@router.get("/stores/{store_id}/sales-history")
def sales(store_id: str, p=Depends(paging), date_from: date | None = None, date_to: date | None = None, product_id: str | None = None, source: str | None = None, service=Depends(get_operational_service)):
    return service.history("sales", store_id, *p, date_from, date_to, product_id, source)


@router.get("/stores/{store_id}/usage-history")
def usage(store_id: str, p=Depends(paging), date_from: date | None = None, date_to: date | None = None, ingredient_id: str | None = None, source: str | None = None, service=Depends(get_operational_service)):
    return service.history("usage", store_id, *p, date_from, date_to, ingredient_id, source)


@router.get("/stores/{store_id}/purchase-history")
def purchases(store_id: str, p=Depends(paging), date_from: date | None = None, date_to: date | None = None, ingredient_id: str | None = None, supplier_id: str | None = None, source: str | None = None, service=Depends(get_operational_service)):
    return service.history("purchase", store_id, *p, date_from, date_to, ingredient_id, source, supplier_id)


@router.get("/stores/{store_id}/inventory")
def inventory(store_id: str, p=Depends(paging), ingredient_id: str | None = None, service=Depends(get_operational_service)):
    return service.inventory(store_id, *p, ingredient_id)


@router.get("/stores/{store_id}/inventory-movements")
def movements(store_id: str, p=Depends(paging), lot_id: str | None = None, ingredient_id: str | None = None, movement_type: str | None = None, service=Depends(get_operational_service)):
    return service.movements(store_id, *p, lot_id, ingredient_id, movement_type)


@router.get("/stores/{store_id}/settings")
def settings(store_id: str, service=Depends(get_operational_service)):
    return service.settings(store_id)


@router.get("/stores/{store_id}/calendar-features")
def calendar(store_id: str, p=Depends(paging), date_from: date | None = None, date_to: date | None = None, service=Depends(get_operational_service)):
    return service.calendar(store_id, *p, date_from, date_to)
