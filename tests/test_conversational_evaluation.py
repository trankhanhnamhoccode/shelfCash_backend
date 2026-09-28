from app.decision_intelligence.conversational_evaluation import capture, chat7_cases, chat7_conversations, markdown_report


def test_chat7_corpus_has_required_categories_and_conversations():
    categories = {case.category for case in chat7_cases()}
    assert {"budget", "budget_what_if", "strategy", "ingredient", "ambiguity"} <= categories
    assert {"budget", "strategy", "ingredient"} == set(chat7_conversations())
    assert all(len(turns) == 3 for turns in chat7_conversations().values())


def test_capture_preserves_fallback_diagnostics_without_inferring_repair():
    result = capture(chat7_cases()[0], {"answer": "fallback", "provider": "deterministic_fallback", "intent": "BUDGET", "grounded": True, "claims": [], "citations": [], "llm_diagnostics": {"failure_stage": "GROUNDING"}})
    assert result.fallback and result.fallback_reason == "GROUNDING"
    assert result.repair == "not_observable"
    assert "GROUNDING" in markdown_report([result], environment={"provider_configured": False})
