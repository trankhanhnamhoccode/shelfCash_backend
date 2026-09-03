from types import SimpleNamespace
import pytest

from app.decision_intelligence.contracts import BriefStrategyEvaluation, BriefStrategyPresentation, BriefStrategyReason, StrategyExpressionItem, StrategyExpressionLLMResponse
from app.decision_intelligence.strategy_expression import StrategyExpressionProvider
from app.decision_intelligence.strategy_presentation import present, present_all


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
    assert result[0].presentation.headline == "An toàn được chọn"
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


def test_diagnostics_are_request_scoped_for_success_fallback_and_skip():
    success = StrategyExpressionProvider(Gateway({"strategies": [{"strategy": "protected", "headline": "An toàn được chọn", "summary": "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", "reason_messages": []}]}), SimpleNamespace()).express_result([row()])
    invalid = StrategyExpressionProvider(Gateway({"strategies": []}), SimpleNamespace()).express_result([row()])
    skipped = StrategyExpressionProvider(None, SimpleNamespace()).express_result([row()])
    assert success.diagnostics["status"] == "success" and success.diagnostics["source"] == "llm"
    assert invalid.diagnostics["status"] == "fallback" and invalid.diagnostics["fallback_used"] is True
    assert invalid.diagnostics["failure_stage"] == "BUSINESS_VALIDATION"
    assert skipped.diagnostics == {"attempted": False, "status": "skipped", "source": "deterministic", "fallback_used": False, "skip_reason": "provider_disabled", "strategy_count": 1, "selected_style_example_ids": []}


def test_result_diagnostics_cannot_be_overwritten_by_later_invocation():
    provider = StrategyExpressionProvider(Gateway({"strategies": [{"strategy": "protected", "headline": "An toàn được chọn", "summary": "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", "reason_messages": []}]}), SimpleNamespace())
    first = provider.express_result([row()])
    second = provider.express_result([])
    assert first.diagnostics["status"] == "success"
    assert second.diagnostics["skip_reason"] == "empty_strategy_set"


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


def test_strategy_expression_rejects_precision_changed_display_with_diagnostics():
    source = row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"purchase_cost_delta": 940000})
    gateway = Gateway({"strategies": [{"strategy": "balanced", "headline": "Cân bằng vẫn là phương án hợp lệ", "summary": "Phương án không được chọn vì chi phí cao hơn.", "reason_messages": ["Chi phí là 9,4 triệu đồng."]}]})
    result = StrategyExpressionProvider(gateway, SimpleNamespace()).express_result([source])
    assert result.presentations[0].presentation == source.presentation
    assert result.diagnostics["failure_stage"] == "GROUNDING"
    assert result.diagnostics["details"]["offending_strategy"] == "balanced"
    assert "9,4 triệu đồng" in result.diagnostics["details"]["offending_numeric_mentions"]


def test_strategy_expression_rejects_named_cross_strategy_label():
    source = row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST", {"selected_purchase_cost": 7_670_000})
    gateway = Gateway({"strategies": [{"strategy": "lean", "headline": "Tiết kiệm được chọn", "summary": "Tiết kiệm rẻ hơn An toàn và có chi phí thấp nhất.", "reason_messages": []}]})
    assert StrategyExpressionProvider(gateway, SimpleNamespace()).express_result([source]).diagnostics["status"] == "fallback"


def assert_deterministic_expression_valid(evaluation):
    rendered = present(evaluation)
    typed = StrategyExpressionLLMResponse(strategies=[StrategyExpressionItem(strategy=evaluation.strategy, headline=rendered.headline, summary=rendered.summary, reason_messages=rendered.reason_messages)])
    StrategyExpressionProvider(None, SimpleNamespace())._validate(typed, [evaluation])


@pytest.mark.parametrize("status,code,values,reason_status", [
    ("selected", "LOWEST_EXACT_VALID_CANDIDATE_COST", {"selected_purchase_cost": 7_670_000}, "verified"),
    ("feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"purchase_cost_delta": 1_730_000}, "verified"),
    ("feasible_not_selected", "STRATEGY_NAME_TIEBREAK", {}, "verified"),
    ("selected", "SELECTION_REASON_UNAVAILABLE", {}, "unavailable"),
    *[("rejected", code, {}, "code_only") for code in ("BUDGET", "SERVICE_LEVEL_REQUIREMENT", "LEAD_TIME", "MOQ", "PACK_SIZE", "SUPPLIER_UNAVAILABLE", "ORDER_CUTOFF", "SUPPLIER_MAX_QUANTITY", "SUPPLIER_MAX_COST", "CAPACITY_CONSEQUENCE", "UNKNOWN_EXPIRY", "EXACT_SIMULATION_SAFETY_FLOOR", "RISK_CONSTRAINT_VIOLATION")],
    ("technical_failure", "EVALUATION_TECHNICAL_FAILURE", {}, "code_only"),
    ("not_evaluated", "EVALUATION_OUTCOME_UNAVAILABLE", {}, "unavailable"),
])
def test_deterministic_strategy_presentations_pass_expression_validator_matrix(status, code, values, reason_status):
    strategy = "protected" if status == "selected" else "balanced" if status in {"feasible_not_selected", "technical_failure"} else "lean"
    assert_deterministic_expression_valid(row(strategy, status, code, values, reason_status))


@pytest.mark.parametrize("code,text", [
    ("MOQ", "Lượng đặt hàng không đáp ứng mức đặt tối thiểu của nhà cung cấp."),
    ("BUDGET", "Chi phí nhập vượt giới hạn ngân sách của kế hoạch."),
    ("LEAD_TIME", "Nhu cầu cần được đáp ứng kịp thời điểm sử dụng."),
])
def test_strategy_expression_reason_collision_wording_is_valid(code, text):
    source = row("lean", "rejected", code)
    typed = StrategyExpressionLLMResponse(strategies=[StrategyExpressionItem(strategy="lean", headline="Tiết kiệm không đáp ứng điều kiện bắt buộc", summary="Phương án không được đưa vào nhóm có thể lựa chọn.", reason_messages=[text])])
    StrategyExpressionProvider(None, SimpleNamespace())._validate(typed, [source])


@pytest.mark.parametrize("source,headline,summary", [
    (row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST"), "Tiết kiệm là phương án được chọn", "Tiết kiệm có chi phí thấp nhất trong các phương án hợp lệ."),
    (row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED"), "Cân bằng vẫn đáp ứng các điều kiện", "Cân bằng không được chọn vì chi phí cao hơn phương án được chọn."),
    (row("balanced", "technical_failure", "EVALUATION_TECHNICAL_FAILURE"), "Chưa thể kết luận đầy đủ về phương án", "Quá trình đánh giá gặp vấn đề kỹ thuật."),
])
def test_strategy_expression_accepts_valid_paraphrases(source, headline, summary):
    typed = StrategyExpressionLLMResponse(strategies=[StrategyExpressionItem(strategy=source.strategy, headline=headline, summary=summary, reason_messages=[])])
    StrategyExpressionProvider(None, SimpleNamespace())._validate(typed, [source])


@pytest.mark.parametrize("code,values,key,expected", [
    ("LOWEST_EXACT_VALID_CANDIDATE_COST", {"selected_purchase_cost": 7_668_000}, "selected_purchase_cost", "7,67 triệu đồng"),
    ("SERVICE_LEVEL_REQUIREMENT", {"observed_fill_rate": .7394, "required_fill_rate": .8}, "observed_fill_rate", "73,94%"),
    ("LEAD_TIME", {"earliest_arrival_date": "2026-08-14"}, "earliest_arrival_date", "14/08"),
])
def test_strategy_display_matches_expression_display(code, values, key, expected):
    source = row("lean", "rejected", code, values)
    deterministic = present(source)
    payload = StrategyExpressionProvider(None, SimpleNamespace())._payload([source])
    display = payload["strategies"][0]["reasons"][0]["display_values"][key]
    assert display == expected
    assert expected in " ".join([deterministic.headline, deterministic.summary, *deterministic.reason_messages])


def expression(strategy, headline, summary, reason_messages=None):
    return {"strategy": strategy, "headline": headline, "summary": summary, "reason_messages": reason_messages or []}


def selected_numeric_source():
    return row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST", {"selected_purchase_cost": 7_670_000})


def selected_numeric_expression(**updates):
    value = expression(
        "lean", "Tiết kiệm được chọn",
        "Đây là phương án hợp lệ có chi phí nhập thấp nhất.",
        ["Chi phí nhập dự kiến là 7,67 triệu đồng."],
    )
    value.update(updates)
    return value


def numeric_result(source, item):
    return StrategyExpressionProvider(Gateway({"strategies": [item]}), SimpleNamespace()).express_result([source])


def test_strategy_expression_accepts_exact_canonical_numeric_display():
    result = numeric_result(selected_numeric_source(), selected_numeric_expression())
    assert result.diagnostics["status"] == "success"


@pytest.mark.parametrize(
    "item,expected_field,expected_kind,mentions",
    [
        (selected_numeric_expression(reason_messages=["Chi phí nhập dự kiến là 7.670.000 đồng."]), "reason_messages[0]", "NUMERIC_REPRESENTATION_CHANGE", ["7.670.000 đồng"]),
        (selected_numeric_expression(summary="Trong 3 phương án, đây là phương án có chi phí nhập thấp nhất."), "summary", "NUMERIC_HALLUCINATION", ["3"]),
        (selected_numeric_expression(reason_messages=["Chi phí cao hơn khoảng 18%. "]), "reason_messages[0]", "NUMERIC_HALLUCINATION", ["18%"]),
        (selected_numeric_expression(headline="Lựa chọn số 1 được chọn"), "headline", "NUMERIC_HALLUCINATION", ["1"]),
    ],
)
def test_strategy_expression_numeric_failures_are_field_scoped(item, expected_field, expected_kind, mentions):
    result = numeric_result(selected_numeric_source(), item)
    assert result.diagnostics["failure_stage"] == "GROUNDING"
    assert result.diagnostics["error_message"].startswith("strategy_expression_unauthorized_number")
    details = result.diagnostics["details"]
    assert details["offending_strategy"] == "lean"
    assert details["offending_field"] == expected_field
    assert details["numeric_failure_kind"] == expected_kind
    assert all(mention in details["offending_numeric_mentions"] for mention in mentions)
    assert details["authorized_numeric_mentions"] == ["7,67 triệu đồng"]


def test_strategy_expression_collects_multiple_offending_numbers():
    result = numeric_result(selected_numeric_source(), selected_numeric_expression(reason_messages=["Trong 3 phương án, chi phí cao hơn 18%."]))
    details = result.diagnostics["details"]
    assert details["offending_field"] == "reason_messages[0]"
    assert details["offending_numeric_mentions"] == ["3", "18%"]
    assert details["numeric_failure_kind"] == "NUMERIC_HALLUCINATION"


def test_strategy_expression_rejects_mixed_authorized_and_unauthorized_numbers():
    result = numeric_result(selected_numeric_source(), selected_numeric_expression(reason_messages=["Chi phí là 7,67 triệu đồng trong 3 phương án."]))
    details = result.diagnostics["details"]
    assert details["offending_numeric_mentions"] == ["3"]
    assert details["numeric_failure_kind"] == "NUMERIC_HALLUCINATION"


def test_strategy_expression_rejects_cross_strategy_numeric_borrow():
    lean = selected_numeric_source()
    balanced = row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"candidate_purchase_cost": 9_400_000, "selected_purchase_cost": 7_670_000, "purchase_cost_delta": 1_730_000})
    result = StrategyExpressionProvider(Gateway({"strategies": [
        expression("lean", "Tiết kiệm được chọn", "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", ["Chi phí là 9,40 triệu đồng."]),
        expression("balanced", "Cân bằng vẫn là phương án hợp lệ", "Không được chọn vì chi phí cao hơn phương án được chọn."),
    ]}), SimpleNamespace()).express_result([lean, balanced])
    details = result.diagnostics["details"]
    assert details["offending_strategy"] == "lean"
    assert details["offending_field"] == "reason_messages[0]"
    assert details["offending_numeric_mentions"] == ["9,40 triệu đồng"]
    assert details["authorized_numeric_mentions"] == ["7,67 triệu đồng"]


def test_strategy_expression_numeric_failure_falls_back_whole_set_without_retry():
    rows = present_all([
        selected_numeric_source(),
        row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"purchase_cost_delta": 1_730_000}),
        row("protected", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST", {"selected_purchase_cost": 9_400_000}),
    ])
    gateway = Gateway({"strategies": [
        expression("lean", "Tiết kiệm được chọn", "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", ["Chi phí là 7,67 triệu đồng."]),
        expression("balanced", "Cân bằng vẫn là phương án hợp lệ", "Không được chọn vì chi phí cao hơn phương án được chọn.", ["Chi phí là 9,4 triệu đồng."]),
        expression("protected", "An toàn được chọn", "Đây là phương án hợp lệ có chi phí nhập thấp nhất.", ["Chi phí là 9,40 triệu đồng."]),
    ]})
    result = StrategyExpressionProvider(gateway, SimpleNamespace()).express_result(rows)
    assert len(gateway.calls) == 1
    assert result.diagnostics["status"] == "fallback"
    assert result.presentations == rows
    assert [entry.presentation for entry in result.presentations] == [entry.presentation for entry in rows]


def test_strategy_expression_payload_has_no_raw_numeric_business_values():
    source = row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED", {"candidate_purchase_cost": 9_398_000, "selected_purchase_cost": 7_668_000, "purchase_cost_delta": 1_730_000, "observed_fill_rate": .7394})
    payload = StrategyExpressionProvider(None, SimpleNamespace())._payload([source])
    rendered = str(payload)
    assert "9398000" not in rendered and "7668000" not in rendered and "0.7394" not in rendered
    assert payload["strategies"][0]["reasons"][0]["display_values"] == {
        "candidate_purchase_cost": "9,4 triệu đồng", "selected_purchase_cost": "7,67 triệu đồng",
        "purchase_cost_delta": "1,73 triệu đồng", "observed_fill_rate": "73,94%",
    }

def test_strategy_expression_classifies_precision_changed_authorized_display():
    source = row(
        "balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED",
        {"authorized_manager_display": "9,40 triệu đồng"},
    )
    item = expression(
        "balanced", "Cân bằng vẫn là phương án hợp lệ",
        "Không được chọn vì chi phí cao hơn phương án được chọn.",
        ["Chi phí là 9,4 triệu đồng."],
    )
    result = numeric_result(source, item)
    details = result.diagnostics["details"]
    assert details["offending_numeric_mentions"] == ["9,4 triệu đồng"]
    assert details["authorized_numeric_mentions"] == ["9,40 triệu đồng"]
    assert details["numeric_failure_kind"] == "NUMERIC_REPRESENTATION_CHANGE"


def llm_items(rows):
    return [expression(item.strategy, item.presentation.headline, item.presentation.summary, item.presentation.reason_messages) for item in rows]


def test_strategy_expression_reason_failure_has_structured_field_diagnostics():
    source = present_all([row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED")])
    item = llm_items(source)[0]
    item["reason_messages"] = ["Phương án có rủi ro tồn kho thấp hơn."]
    result = StrategyExpressionProvider(Gateway({"strategies": [item]}), SimpleNamespace()).express_result(source)
    assert result.diagnostics["failure_stage"] == "GROUNDING"
    assert result.diagnostics["details"] == {
        "offending_strategy": "balanced", "offending_field": "reason_messages[0]",
        "detected_phrase": "rủi ro tồn kho thấp hơn",
        "authorized_reason_codes": ["HIGHER_PURCHASE_COST_THAN_SELECTED"],
    }


@pytest.mark.parametrize("code,message,phrase", [
    ("MOQ", "Nhà cung cấp không thể đáp ứng đơn hàng.", "nhà cung cấp không thể đáp ứng"),
    ("BUDGET", "Mức đáp ứng nhu cầu thấp hơn yêu cầu.", "mức đáp ứng nhu cầu thấp hơn yêu cầu"),
    ("CAPACITY_NOT_EVALUATED", "Kho bị quá tải.", "kho bị quá tải"),
])
def test_strategy_expression_rejects_unauthorized_supplier_service_and_capacity_reasons(code, message, phrase):
    source = present_all([row("lean", "rejected", code)])
    item = llm_items(source)[0]
    item["reason_messages"] = [message]
    result = StrategyExpressionProvider(Gateway({"strategies": [item]}), SimpleNamespace()).express_result(source)
    assert result.diagnostics["failure_stage"] == "GROUNDING"
    assert result.diagnostics["details"]["offending_field"] == "reason_messages[0]"
    assert result.diagnostics["details"]["detected_phrase"] == phrase


def test_strategy_expression_accepts_moq_supplier_noun_without_supplier_unavailable_reason():
    source = row("lean", "rejected", "MOQ")
    typed = StrategyExpressionLLMResponse(strategies=[StrategyExpressionItem(
        strategy="lean", headline="Tiết kiệm không đáp ứng điều kiện bắt buộc",
        summary="Phương án không được đưa vào nhóm có thể lựa chọn.",
        reason_messages=["Lượng đặt hàng không đáp ứng mức đặt tối thiểu của nhà cung cấp."],
    )])
    StrategyExpressionProvider(None, SimpleNamespace())._validate(typed, [source])


def test_strategy_expression_entity_failure_has_structured_diagnostics_and_named_pairwise_rejects():
    source = present_all([row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST")])
    item = llm_items(source)[0]
    item["summary"] = "Tiết kiệm rẻ hơn An toàn và có chi phí thấp nhất trong các phương án hợp lệ."
    result = StrategyExpressionProvider(Gateway({"strategies": [item]}), SimpleNamespace()).express_result(source)
    assert result.diagnostics["failure_stage"] == "GROUNDING"
    assert result.diagnostics["details"] == {
        "offending_strategy": "lean", "offending_field": "summary",
        "offending_entity": "an toàn", "authorized_entities": ["Tiết kiệm"],
    }


def test_strategy_expression_accepts_own_label_generic_group_and_selected_relation():
    selected = row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST")
    balanced = row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED")
    typed = StrategyExpressionLLMResponse(strategies=[
        StrategyExpressionItem(strategy="lean", headline="Tiết kiệm được chọn", summary="Tiết kiệm có chi phí nhập thấp nhất trong các phương án hợp lệ.", reason_messages=[]),
        StrategyExpressionItem(strategy="balanced", headline="Cân bằng vẫn là phương án hợp lệ", summary="Cân bằng có chi phí cao hơn phương án được chọn nên không được chọn.", reason_messages=[]),
    ])
    StrategyExpressionProvider(None, SimpleNamespace())._validate(typed, [selected, balanced])


@pytest.mark.parametrize("failure", ["reason", "entity", "semantic"])
def test_strategy_expression_failure_falls_back_whole_set_without_retry_or_partial_salvage(failure):
    rows = present_all([
        row("lean", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST"),
        row("balanced", "feasible_not_selected", "HIGHER_PURCHASE_COST_THAN_SELECTED"),
        row("protected", "selected", "LOWEST_EXACT_VALID_CANDIDATE_COST"),
    ])
    items = llm_items(rows)
    if failure == "reason":
        items[1]["reason_messages"] = ["Phương án có rủi ro tồn kho thấp hơn."]
    elif failure == "entity":
        items[1]["summary"] = "Cân bằng có chi phí cao hơn Tiết kiệm nên không được chọn nhưng vẫn là phương án hợp lệ."
    else:
        items[1]["headline"] = "Cân bằng không được chọn"
        items[1]["summary"] = "Chi phí cao hơn phương án được chọn."
    gateway = Gateway({"strategies": items})
    result = StrategyExpressionProvider(gateway, SimpleNamespace()).express_result(rows)
    assert len(gateway.calls) == 1
    assert result.diagnostics["status"] == "fallback"
    assert [item.presentation for item in result.presentations] == [item.presentation for item in rows]
