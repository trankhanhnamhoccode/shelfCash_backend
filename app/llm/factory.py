from __future__ import annotations

from app.llm.openrouter_qwen import OpenRouterQwenProvider


def create_llm_provider(settings) -> OpenRouterQwenProvider:
    return OpenRouterQwenProvider(settings)
