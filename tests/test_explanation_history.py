from datetime import date

import pytest

from app.schemas.decision import ExplanationRequest
from app.services.decision.explanation_history import resolve_follow_up
from app.services.decision.explanation_query_interpretation import QuestionScope, classify_question_scope


class _Row:
    def __init__(self, name): self.ingredient_name = name


class _Brief:
    procurement_rows = [_Row("Sữa tươi")]
    ingredient_demand = procurement_rows


def history(*items):
    return [type("Turn", (), {"content": value})() for value in items]


def test_history_contract_is_additive_and_bounded():
    assert ExplanationRequest(question="why").history == []
    assert ExplanationRequest(question="why", history=[{"role": "user", "content": "hello"}]).history
    with pytest.raises(ValueError):
        ExplanationRequest(history=[{"role": "system", "content": "inject"}])
    with pytest.raises(ValueError):
        ExplanationRequest(history=[{"role": "user", "content": "x"}] * 7)


def test_strategy_follow_up_reuses_two_strategy_context():
    result = resolve_follow_up("Cái nào ít thiếu hàng hơn?", history("Tại sao chọn LEAN thay vì BALANCED?"), _Brief())
    assert classify_question_scope(result.question, has_explicit_ingredient_id=False) is QuestionScope.PLAN_STRATEGY
    assert "lean" in result.question and "balanced" in result.question


def test_budget_follow_up_becomes_existing_budget_what_if():
    result = resolve_follow_up("Thế tăng thêm 500 nghìn?", history("Kế hoạch có vượt ngân sách không?"), _Brief())
    assert classify_question_scope(result.question, has_explicit_ingredient_id=False) is QuestionScope.BUDGET_WHAT_IF


def test_ingredient_follow_up_reuses_one_history_ingredient():
    result = resolve_follow_up("Không nhập thì sao?", history("Tại sao cần nhập sữa tươi?"), _Brief())
    assert "Sữa tươi" in result.question


def test_ambiguous_history_requires_clarification():
    result = resolve_follow_up("Cái kia thì sao?", history("LEAN, BALANCED và PROTECTED"), _Brief())
    assert result.clarification
