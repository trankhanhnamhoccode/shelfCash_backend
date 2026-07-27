from app.llm.base import LLMProvider


class DisabledLLMProvider(LLMProvider):
    @property
    def available(self) -> bool:
        return False

    def health(self) -> dict:
        return {"provider": "disabled", "available": False, "loaded": False}

    async def map_sheet(self, profile, canonical_schemas, rule_suggestion):
        result = rule_suggestion.model_copy(deep=True)
        result.source = "rule_fallback"
        result.requires_review = True
        return result
