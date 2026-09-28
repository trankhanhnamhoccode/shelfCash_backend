import pytest

from app.schemas.decision import ExplanationRequest
from app.services.decision.budget_what_if import current_budget_snapshot, resolve_budget_mutation
from app.services.decision.explain_decision import ExplainDecision
from app.services.decision.explanation_query_interpretation import QuestionScope, classify_question_scope


@pytest.mark.parametrize(("question", "expected"), (
    ("Nếu tôi tăng thêm 500 nghìn thì sao?", 10_500_000),
    ("Tăng ngân sách thêm 1 triệu thì thế nào?", 11_000_000),
    ("Nếu giảm ngân sách 2 triệu?", 8_000_000),
    ("Nếu budget là 12 triệu thì sao?", 12_000_000),
    ("Nếu chỉ có 8 triệu thì sao?", 8_000_000),
    ("Nếu budget là 12tr thì sao?", 12_000_000),
    ("Nếu budget là 1000000 thì sao?", 1_000_000),
))
def test_budget_what_if_parses_deterministic_money(question, expected):
    assert classify_question_scope(question, has_explicit_ingredient_id=False) is QuestionScope.BUDGET_WHAT_IF
    assert resolve_budget_mutation(question, current_budget=10_000_000).budget_limit == expected


def test_ambiguous_and_missing_snapshot_fail_closed():
    assert resolve_budget_mutation("Tăng budget chút thì sao?", current_budget=10_000_000).issue == "amount_required"
    assert resolve_budget_mutation("Nếu tôi tăng thêm 500 nghìn thì sao?", current_budget=None).issue == "current_budget_unavailable"
    assert current_budget_snapshot({"technical_metrics": {"scenario_diagnostics": {}}}) is None


def test_budget_question_bridge_passes_backend_calculated_value_to_existing_what_if():
    calls = []
    package = {"technical_metrics": {"scenario_diagnostics": {"budget_snapshot": {"budget_limit": 10_000_000}}}}

    def what_if(run_id, body):
        calls.append((run_id, body.budget_limit))
        return {"grounded_explanation": {
            "source": "deterministic_fallback", "language": "vi", "detail_level": "simple",
            "summary": "What-if result", "why_this_plan": ["What-if result"], "main_risks": [], "tradeoffs": [],
            "important_assumptions": [], "decision_run_id": run_id, "answer": "What-if result",
            "intent": "WHAT_IF", "entities": {"ingredient_ids": [], "supplier_ids": []},
            "claims": [], "citations": [], "grounded": True, "provider": "deterministic_fallback",
        }}

    reader = type("Reader", (), {"read": staticmethod(lambda _run_id: package)})()
    service = ExplainDecision(None, reader, None, None, what_if_decision=what_if)
    response = service.explain("run-1", ExplanationRequest(question="Nếu tôi tăng thêm 500 nghìn thì sao?"))
    assert calls == [("run-1", 10_500_000)]
    assert response["intent"] == "WHAT_IF"


def test_non_budget_what_if_and_strategy_question_do_not_use_budget_bridge():
    assert classify_question_scope("Nếu nhà cung cấp trễ 2 ngày thì sao?", has_explicit_ingredient_id=False) is not QuestionScope.BUDGET_WHAT_IF
    assert classify_question_scope("Tại sao chọn LEAN?", has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY
