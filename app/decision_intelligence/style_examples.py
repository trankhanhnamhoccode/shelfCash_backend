"""Deterministic, non-authoritative wording examples for LLM narration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StyleExample:
    example_id: str
    task: str
    intent: str
    case: str
    style: str
    template: str
    negative: bool = False


_BANK = (
    StyleExample("summary-feasible", "overall_summary", "SUMMARY", "FEASIBLE", "simple", "ShelfCash đề xuất kế hoạch <STRATEGY> với chi phí nhập <COST> cho <HORIZON>."),
    StyleExample("summary-risk", "overall_summary", "SUMMARY", "FEASIBLE_WITH_RISK", "simple", "Cần lưu ý <INGREDIENT> có thể bắt đầu thiếu từ <DATE>."),
    StyleExample("summary-limit", "overall_summary", "SUMMARY", "WITH_LIMITATION", "simple", "<LIMITATION>."),
    StyleExample("summary-no-feasible", "overall_summary", "SUMMARY", "NO_FEASIBLE", "simple", "Hiện chưa có kế hoạch nhập hàng khả thi."),
    StyleExample("why-causal", "decision_narrative", "WHY_PROCUREMENT", "CAUSAL_AVAILABLE", "simple", "Kế hoạch đề xuất nhập <QUANTITY> <UNIT> <INGREDIENT> vì <VERIFIED_REASON>."),
    StyleExample("why-no-causal", "decision_narrative", "WHY_PROCUREMENT", "CAUSAL_UNAVAILABLE", "simple", "Chưa đủ dữ liệu để xác nhận nguyên nhân này."),
    StyleExample("why-no-causal-negative", "decision_narrative", "WHY_PROCUREMENT", "CAUSAL_UNAVAILABLE", "simple", "<INGREDIENT> có thể thiếu từ <DATE> do nhu cầu tăng.", True),
    StyleExample("quantity", "decision_narrative", "PROCUREMENT_QUANTITY", "DEFAULT", "simple", "Kế hoạch đề xuất nhập <QUANTITY> <UNIT> <INGREDIENT>."),
    StyleExample("demand-horizon", "decision_narrative", "DEMAND_HORIZON", "DEFAULT", "simple", "Nhu cầu dự kiến của <INGREDIENT> trong <HORIZON> là <VALUE>."),
    StyleExample("demand-day", "decision_narrative", "DEMAND_DAY", "DEFAULT", "simple", "Ngày <DATE>, nhu cầu dự kiến của <INGREDIENT> là <VALUE>."),
    StyleExample("risk", "decision_narrative", "RISK", "DEFAULT", "simple", "<INGREDIENT> có thể bắt đầu thiếu từ <DATE>."),
    StyleExample("plan", "decision_narrative", "PLAN", "DEFAULT", "simple", "Kế hoạch hiện tại sử dụng chiến lược <STRATEGY>."),
    StyleExample("what-if-strategy", "what_if", "WHAT_IF", "STRATEGY", "simple", "Nếu chuyển sang <STRATEGY>, kết quả mô phỏng thay đổi như sau: <OUTCOME>."),
    StyleExample("what-if-cost", "what_if", "WHAT_IF", "COST_DELTA", "simple", "Trong mô phỏng, chi phí là <VALUE>, chênh <DELTA> so với hiện tại."),
    StyleExample("what-if-generic", "what_if", "WHAT_IF", "DEFAULT", "simple", "Nếu <MUTATION>, <OUTCOME>."),
)


def retrieve_style_examples(*, task: str, intent: str, case: str, detail_level: str, limit: int = 1) -> list[dict[str, str | bool]]:
    """Return at most one positive and one explicitly-labelled negative pattern."""
    matches = [item for item in _BANK if item.task == task and item.intent == intent and item.case == case and item.style == detail_level]
    if not matches:
        matches = [item for item in _BANK if item.task == task and item.intent == intent and item.case == "DEFAULT" and item.style == detail_level]
    return [
        {"example_id": item.example_id, "template": item.template, "negative": item.negative}
        for item in matches[:max(0, min(limit, 2))]
    ]
