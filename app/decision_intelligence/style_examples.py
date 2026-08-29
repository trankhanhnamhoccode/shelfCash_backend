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
    # Ingredient examples are non-authoritative wording patterns only.
    StyleExample("ingredient-stockout-before-receipt", "ingredient_synthesis", "SYNTHESIS", "STOCKOUT_BEFORE_RECEIPT", "simple", "<INGREDIENT> cần được theo dõi sát khi mô phỏng ghi nhận nguy cơ thiếu từ <DATE>. Lô nhập <ORDER_QUANTITY> dự kiến đến <ARRIVAL_DATE>."),
    StyleExample("ingredient-material-shortage", "ingredient_synthesis", "SYNTHESIS", "MATERIAL_SHORTAGE", "simple", "<INGREDIENT> có thiếu hụt đáng chú ý trong kỳ mô phỏng. Mức thiếu được ghi nhận là <SHORTAGE>."),
    StyleExample("ingredient-shortage-with-order", "ingredient_synthesis", "SYNTHESIS", "SHORTAGE_WITH_ORDER", "simple", "<INGREDIENT> có nguy cơ thiếu trong kỳ mô phỏng; kế hoạch hiện có lô nhập <ORDER_QUANTITY> để theo dõi cùng rủi ro này."),
    StyleExample("ingredient-limited-evidence", "ingredient_synthesis", "SYNTHESIS", "LIMITED_EVIDENCE", "simple", "<INGREDIENT> có rủi ro vận hành cần ưu tiên theo dõi. Dữ liệu hiện có chỉ đủ để mô tả tín hiệu này, không xác nhận nguyên nhân."),
    StyleExample("ingredient-critical-generic", "ingredient_synthesis", "SYNTHESIS", "DEFAULT", "simple", "<INGREDIENT> có tín hiệu vận hành cần được ưu tiên theo dõi trong kỳ kế hoạch."),
    StyleExample("summary-feasible", "overall_summary", "SUMMARY", "FEASIBLE", "simple", "ShelfCash đề xuất kế hoạch <STRATEGY> với chi phí nhập <COST> cho <HORIZON>."),
    StyleExample("summary-selected-plan-risk", "overall_summary", "SUMMARY", "SELECTED_PLAN_RISK", "simple", "Trong kế hoạch hiện tại, <INGREDIENT> có nguy cơ thiếu từ <DATE>."),
    StyleExample("summary-conservative-design-risk", "overall_summary", "SUMMARY", "CONSERVATIVE_DESIGN_RISK", "simple", "Trong kịch bản nhu cầu bảo thủ, <INGREDIENT> có nguy cơ thiếu từ <DATE>."),
    StyleExample("summary-stress-risk", "overall_summary", "SUMMARY", "STRESS_RISK", "simple", "Trong kịch bản kiểm tra sức chịu đựng, <INGREDIENT> có nguy cơ thiếu."),
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
