from fastapi import APIRouter, Depends, Request

from app.core.exceptions import LLMUnavailableError
from app.core.canonical_schemas import CANONICAL_SCHEMAS
from app.core.rule_mapper import map_sheet_rules
from app.dependencies import get_llm_provider, require_api_key
from app.schemas.llm import MapSheetRequest, MappingSuggestion

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/health")
def llm_health(provider=Depends(get_llm_provider)):
    return provider.health()


@router.post("/map-sheet", response_model=MappingSuggestion, dependencies=[Depends(require_api_key)])
async def map_sheet(
    payload: MapSheetRequest,
    request: Request,
    provider=Depends(get_llm_provider),
):
    threshold = request.app.state.settings.rule_confidence_threshold
    rule = map_sheet_rules(payload.profile, threshold)
    if provider.available:
        try:
            return await provider.map_sheet(payload.profile, CANONICAL_SCHEMAS, rule)
        except LLMUnavailableError:
            raise
        except Exception as exc:
            if rule.confidence >= threshold or (rule.sheet_type != "unknown" and rule.column_mapping):
                rule.source = "rule_fallback"
                rule.requires_review = True
                rule.warnings.append(f"LLM mapping failed ({type(exc).__name__}: {str(exc)}); rule suggestion retained")
                rule.raw_response = {"error": str(exc), "details": getattr(exc, "details", None)}
                return rule
            raise LLMUnavailableError()
    if rule.confidence >= threshold or (rule.sheet_type != "unknown" and rule.column_mapping):
        rule.source = "rule_fallback"
        rule.requires_review = True
        return rule
    raise LLMUnavailableError()
