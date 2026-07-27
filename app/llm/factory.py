from app.llm.disabled import DisabledLLMProvider


def create_llm_provider(settings):
    if settings.llm_provider == "disabled":
        return DisabledLLMProvider()
    if settings.llm_provider == "local_qwen":
        from app.llm.local_qwen import LocalQwenProvider
        return LocalQwenProvider(settings)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
