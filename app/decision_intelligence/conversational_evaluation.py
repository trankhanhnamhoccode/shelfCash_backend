"""Offline harness for CHAT-7 conversational-quality evaluation.

It is development support only: it calls an injected explanation function and
never changes provider settings, persistence, routing, or customer history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    category: str
    history: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    category: str
    question: str
    answer: str | None
    provider: str | None
    intent: str | None
    grounded: bool | None
    fallback: bool
    fallback_reason: str | None
    repair: str
    citations: int
    claims: int
    diagnostics: dict[str, Any]


def chat7_cases() -> tuple[EvaluationCase, ...]:
    rows = (
        ("budget-over", "Kế hoạch có vượt quá ngân sách không?", "budget"),
        ("budget-short", "Có lố budget không?", "budget"),
        ("budget-remaining", "Còn dư bao nhiêu tiền?", "budget"),
        ("budget-overage", "Vượt bao nhiêu?", "budget"),
        ("budget-utilization", "Đã dùng bao nhiêu phần trăm ngân sách?", "budget"),
        ("budget-absolute", "Nếu budget là 12 triệu?", "budget_what_if"),
        ("strategy-selection", "Tại sao chọn LEAN thay vì BALANCED?", "strategy"),
        ("strategy-risk", "Cái nào ít thiếu hàng hơn?", "strategy"),
        ("strategy-cost", "Chênh nhau bao nhiêu tiền?", "strategy"),
        ("strategy-tradeoff", "LEAN đánh đổi điều gì?", "strategy"),
        ("ingredient-why", "Tại sao cần nhập sữa tươi?", "ingredient"),
        ("ingredient-without", "Không nhập thì sao?", "ingredient"),
        ("ingredient-quantity", "Cần bao nhiêu?", "ingredient"),
        ("natural-lean", "Sao lại chọn lean?", "natural_vi"),
        ("natural-budget", "Kế hoạch này có căng tiền không?", "natural_vi"),
        ("ambiguous-referent", "Cái kia thì sao?", "ambiguity"),
        ("ambiguous-change", "Vậy tăng lên thì sao?", "ambiguity"),
        ("ambiguous-value", "Có đáng không?", "ambiguity"),
    )
    return tuple(EvaluationCase(*row) for row in rows)


def chat7_conversations() -> dict[str, tuple[EvaluationCase, ...]]:
    return {
        "budget": (
            EvaluationCase("a1", "Kế hoạch có vượt ngân sách không?", "budget"),
            EvaluationCase("a2", "Nếu tăng thêm 500 nghìn thì sao?", "budget_what_if"),
            EvaluationCase("a3", "Thế còn 200 nghìn?", "budget_what_if"),
        ),
        "strategy": (
            EvaluationCase("b1", "Tại sao chọn LEAN thay vì BALANCED?", "strategy"),
            EvaluationCase("b2", "Cái nào ít thiếu hàng hơn?", "strategy"),
            EvaluationCase("b3", "Nếu tôi ưu tiên dòng tiền thì sao?", "strategy"),
        ),
        "ingredient": (
            EvaluationCase("c1", "Tại sao cần nhập sữa tươi?", "ingredient"),
            EvaluationCase("c2", "Không nhập thì sao?", "ingredient"),
            EvaluationCase("c3", "Cần bao nhiêu?", "ingredient"),
        ),
    }


def capture(case: EvaluationCase, response: dict[str, Any]) -> EvaluationResult:
    diagnostics = response.get("llm_diagnostics") if isinstance(response.get("llm_diagnostics"), dict) else {}
    provider = response.get("provider")
    fallback = provider == "deterministic_fallback"
    failure_stage = diagnostics.get("failure_stage")
    answer = response.get("answer") if isinstance(response.get("answer"), str) else None
    # CHAT-5 currently exposes repaired answer text rather than a separate
    # counter. Preserve this explicit "not_observable" state rather than infer.
    return EvaluationResult(case.case_id, case.category, case.question, answer, provider,
        response.get("intent"), response.get("grounded"), fallback,
        str(failure_stage) if failure_stage else None, "not_observable",
        len(response.get("citations") or []), len(response.get("claims") or []), diagnostics)


def markdown_report(results: list[EvaluationResult], *, environment: dict[str, Any]) -> str:
    fallback = sum(row.fallback for row in results)
    lines = ["# CHAT-7 Conversational Quality Evaluation", "", "## Environment", ""]
    lines += [f"- {key}: `{value}`" for key, value in environment.items()]
    lines += ["", "## Results", "", f"- Cases: {len(results)}", f"- Fallbacks: {fallback}", "", "| Case | Category | Provider | Intent | Grounded | Fallback reason | Answer |", "|---|---|---|---|---|---|---|"]
    for row in results:
        answer = (row.answer or "-").replace("|", "\\|")
        lines.append(f"| {row.case_id} | {row.category} | {row.provider or '-'} | {row.intent or '-'} | {row.grounded} | {row.fallback_reason or '-'} | {answer} |")
    return "\n".join(lines) + "\n"


__all__ = ["EvaluationCase", "EvaluationResult", "capture", "chat7_cases", "chat7_conversations", "markdown_report"]
