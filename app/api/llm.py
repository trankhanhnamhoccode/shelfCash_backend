from fastapi import APIRouter, Depends

from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.rule_mapper import map_sheet_rules
from app.dependencies import get_llm_provider, require_api_key
from app.schemas.llm import MapSheetRequest, MappingSuggestion

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/health")
def llm_health(provider=Depends(get_llm_provider)):
    return provider.health()


@router.post("/map-sheet", response_model=MappingSuggestion, dependencies=[Depends(require_api_key)])
async def map_sheet(payload: MapSheetRequest, provider=Depends(get_llm_provider)):
    rule = map_sheet_rules(payload.profile)
    if provider.available:
        return await provider.map_sheet(payload.profile, CANONICAL_SCHEMAS, rule)
    rule.source = "rule_fallback"
    rule.requires_review = True
    return rule
