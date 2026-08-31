from types import SimpleNamespace

from app.decision_intelligence.contracts import BriefStrategyEvaluation, BriefStrategyPresentation, BriefStrategyReason
from app.decision_intelligence.strategy_expression import StrategyExpressionProvider


class Gateway:
    available = True
    def __init__(self, response): self.response, self.calls = response, []
    def generate_json_sync(self, system, payload, **kwargs):
        self.calls.append((system, payload, kwargs))
        if isinstance(self.response, Exception): raise self.response
        return self.response


def row(strategy="protected", status="selected", code="LOWEST_EXACT_VALID_CANDIDATE_COST", values=None, reason_status="verified"):
    labels = {"lean": "Tiết kiệm", "balanced": "Cân bằng", "protected": "An toàn"}
    return BriefStrategyEvaluation(strategy=strategy, label=labels[strategy], status=status,
        selected=status == "selected", feasible=True if status in {"selected", "feasible_not_selected"} else None,
        reason_status=reason_status, reasons=[BriefStrategyReason(kind="selection", code=code, values=values or {})],
        presentation=BriefStrategyPresentation(headline="frozen", summary="frozen summary", reason_messages=["frozen reason"]))


def test_selected_grounded_success_and_payload_is_manager_safe():
    source = row(values={"selected_purchase_cost": 4_680_000})
    gateway = Gateway({"strategies": [{"strategy": "protected", "headline": "An toàn được chọn", "summary": "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", "reason_messages": ["Chi phí nhập dự kiến là 4,68 triệu đồng."]}]})
    provider = StrategyExpressionProvider(gateway, SimpleNamespace())
    result = provider.express([source], "run")
    assert result[0].presentation.headline == "An toàn được chọn", provider.last_diagnostics
    payload = gateway.calls[0][1]
    rendered = str(payload)
    assert "critic" not in rendered and "evidence_id" not in rendered and "strategies" in payload
    assert payload["strategies"][0]["reasons"][0]["display_values"]["selected_purchase_cost"] == "4,68 triệu đồng"


def test_feasible_failure_and_invented_number_use_whole_set_fallback():
    one = row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"purchase_cost_delta": 324000})
    two = row("lean", "rejected", "BUDGET", {"planned_cost": 5_400_000, "budget_limit": 5_000_000})
    gateway = Gateway({"strategies": [
        {"strategy": "balanced", "headline": "Cân bằng thất bại", "summary": "Chi phí cao.", "reason_messages": []},
        {"strategy": "lean", "headline": "Tiết kiệm không đáp ứng ngân sách", "summary": "Chi phí là 5,5 triệu đồng.", "reason_messages": []},
    ]})
    result = StrategyExpressionProvider(gateway, SimpleNamespace()).express([one, two])
    assert [item.presentation for item in result] == [item.presentation for item in [one, two]]
    assert StrategyExpressionProvider(gateway, SimpleNamespace()).last_diagnostics == {}


def test_technical_infeasible_and_missing_strategy_fall_back():
    source = row("balanced", "technical_failure", "EVALUATION_TECHNICAL_FAILURE")
    gateway = Gateway({"strategies": [{"strategy": "balanced", "headline": "Cân bằng không khả thi", "summary": "Lỗi kỹ thuật.", "reason_messages": []}]})
    result = StrategyExpressionProvider(gateway, SimpleNamespace()).express([source])
    assert result[0].presentation == source.presentation
    missing = StrategyExpressionProvider(Gateway({"strategies": []}), SimpleNamespace()).express([source])
    assert missing[0].presentation == source.presentation


def test_disabled_and_empty_make_zero_calls():
    disabled = Gateway({}) ; disabled.available = False
    assert StrategyExpressionProvider(disabled, SimpleNamespace()).express([row()])[0].presentation.headline == "frozen"
    assert not disabled.calls
    enabled = Gateway({})
    assert StrategyExpressionProvider(enabled, SimpleNamespace()).express([]) == []
    assert not enabled.calls
