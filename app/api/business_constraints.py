from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.core.business_constraints import constraint_type_catalog
from app.dependencies import require_api_key


class ConstraintTypeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    constraint_type: str
    aliases: list[str]
    scope: str
    dimension: str
    ingredient_required: bool
    unit_required: bool
    allowed_units: list[str]
    canonical_unit: str | None
    minimum_value: float
    maximum_value: float | None
    planner_support: str
    resolution_priority: int | None


class ConstraintTypeCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ConstraintTypeResponse]


router = APIRouter(tags=["business-constraints"], dependencies=[Depends(require_api_key)])


@router.get(
    "/business-constraint-types",
    response_model=ConstraintTypeCatalogResponse,
    summary="Discover supported business constraint types",
    description="Returns the canonical type, aliases, scope, dimension, unit/value rules, and planning support status from the server registry.",
)
def business_constraint_types():
    return {"items": constraint_type_catalog()}
