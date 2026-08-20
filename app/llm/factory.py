from __future__ import annotations

from app.llm.openrouter_qwen import OpenRouterLLMGateway


def create_llm_provider(settings) -> OpenRouterLLMGateway:
    return OpenRouterLLMGateway(settings)
