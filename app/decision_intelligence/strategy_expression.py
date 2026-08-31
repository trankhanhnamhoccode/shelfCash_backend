"""Optional, grounded wording for the already-authoritative strategy brief."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.decision_intelligence.contracts import (
    BriefStrategyEvaluation, BriefStrategyPresentation, StrategyExpressionLLMResponse,
)
from app.decision_intelligence.display import purchase_cost_display, vi_number
from app.llm.runtime import generate_json_sync
from app.llm.tasks import LLMFailureStage, LLMTask

SYSTEM_PROMPT = """Ban chi duoc dien dat ngan gon bang tieng Viet cac facts trong payload.
Khong tinh toan, suy luan, doi trang thai, them ly do, them so, ngay, thuc the hay thuat ngu noi bo.
COMMUNICATION_PLAN la bat buoc. Style examples chi la phong cach, khong phai facts.
Tra ve JSON dung schema. Khong dung: critic, solver, candidate, objective, M4, FEFO,
CVAR, Monte Carlo, semantic evidence, selection proof, grounding, OpenRouter, Qwen,
lexical ordering, ty le lap kho.
"""

_FORBIDDEN = ("critic", "solver", "candidate", "objective", "m4", "fefo", "cvar", "monte carlo", "semantic evidence", "selection proof", "grounding", "openrouter", "qwen", "lexical ordering", "tỷ lệ lấp kho", "ty le lap kho")
_THEMES = {
    "BUDGET": ("ngân sách",), "SERVICE_LEVEL_REQUIREMENT": ("mức đáp ứng", "tỷ lệ đáp ứng", "nhu cầu"),
    "HIGHER_PURCHASE_COST_THAN_SELECTED": ("cao hơn phương án được chọn", "chênh lệch"), "LOWEST_EXACT_VALID_CANDIDATE_COST": ("thấp nhất",),
    "STRATEGY_NAME_TIEBREAK": ("cùng chi phí", "phân định"), "LEAD_TIME": ("giao hàng", "kịp thời điểm", "ngày nhận"),
    "MOQ": ("đặt tối thiểu",), "PACK_SIZE": ("quy cách đóng gói",), "SUPPLIER_UNAVAILABLE": ("nhà cung cấp",),
    "ORDER_CUTOFF": ("thời điểm có thể đặt",), "SUPPLIER_MAX_QUANTITY": ("giới hạn số lượng",),
    "SUPPLIER_MAX_COST": ("giới hạn", "giá trị đơn hàng"), "CAPACITY_CONSEQUENCE": ("giới hạn tồn kho",),
    "UNKNOWN_EXPIRY": ("hạn sử dụng",), "EXACT_SIMULATION_SAFETY_FLOOR": ("mức đáp ứng",),
    "RISK_CONSTRAINT_VIOLATION": ("giới hạn rủi ro",), "EVALUATION_TECHNICAL_FAILURE": ("lỗi kỹ thuật",),
    "SELECTION_REASON_UNAVAILABLE": ("chưa đủ dữ liệu",), "EVALUATION_OUTCOME_UNAVAILABLE": ("chưa có đủ kết quả", "chưa đủ dữ liệu"),
}


def _display(key: str, value: object) -> str | None:
    if key.endswith("date") and isinstance(value, str):
        return f"{value[8:10]}/{value[5:7]}" if len(value) == 10 else value
    if "rate" in key and isinstance(value, (int, float)):
        return f"{vi_number(float(value) * 100, 2)}%"
    if any(word in key for word in ("cost", "budget")) and isinstance(value, (int, float)):
        if 0 < value < 1_000_000:
            return f"{vi_number(value / 1000, 0)} nghìn đồng"
        return purchase_cost_display(value)
    if isinstance(value, (int, float)):
        return vi_number(value, 2)
    return value if isinstance(value, str) else None


def strategy_communication_plan(rows: list[BriefStrategyEvaluation]) -> list[dict[str, Any]]:
    return [{
        "strategy": row.strategy, "status": row.status,
        "headline_role": {"selected": "selected", "feasible_not_selected": "feasible_not_selected", "rejected": "required_conditions_not_met", "technical_failure": "evaluation_incomplete", "not_evaluated": "evaluation_unavailable"}[row.status],
        "authorized_reason_codes": [reason.code for reason in row.reasons],
        "authorized_value_fields": sorted({key for reason in row.reasons for key in reason.values}),
        "required_semantics": _required(row), "forbidden_semantics": _forbidden(row),
    } for row in rows]


def _required(row: BriefStrategyEvaluation) -> list[str]:
    if row.status == "selected": return ["selected", "selection_reason_uncertain" if row.reason_status == "unavailable" else "verified_selection_reason"]
    if row.status == "feasible_not_selected": return ["feasible", "not_selected", "verified_selector_reason"]
    if row.status == "rejected": return ["required_conditions_not_met"]
    if row.status == "technical_failure": return ["evaluation_incomplete_not_business_infeasibility"]
    return ["insufficient_evaluation_no_feasibility_conclusion"]


def _forbidden(row: BriefStrategyEvaluation) -> list[str]:
    if row.status == "feasible_not_selected": return ["failed", "infeasible", "rejected"]
    if row.status in {"technical_failure", "not_evaluated"}: return ["business_infeasibility"]
    if row.status == "selected" and row.reason_status == "unavailable": return ["lowest_cost_claim"]
    return []


@dataclass(frozen=True)
class StrategyExpressionResult:
    presentations: list[BriefStrategyEvaluation]
    diagnostics: dict[str, Any]


class StrategyExpressionProvider:
    def __init__(self, llm_provider, settings):
        self.llm_provider, self.settings = llm_provider, settings

    def express(self, rows: list[BriefStrategyEvaluation], decision_run_id: str | None = None) -> list[BriefStrategyEvaluation]:
        """Compatibility wrapper; callers needing audit data use express_result."""
        return self.express_result(rows, decision_run_id).presentations

    def express_result(self, rows: list[BriefStrategyEvaluation], decision_run_id: str | None = None) -> "StrategyExpressionResult":
        fallback = rows
        if not rows:
            return StrategyExpressionResult(fallback, {"attempted": False, "status": "skipped", "source": "deterministic", "fallback_used": False, "skip_reason": "empty_strategy_set", "strategy_count": 0, "selected_style_example_ids": []})
        if not self.llm_provider:
            return StrategyExpressionResult(fallback, {"attempted": False, "status": "skipped", "source": "deterministic", "fallback_used": False, "skip_reason": "provider_disabled", "strategy_count": len(rows), "selected_style_example_ids": []})
        if not getattr(self.llm_provider, "available", False):
            return StrategyExpressionResult(fallback, {"attempted": False, "status": "skipped", "source": "deterministic", "fallback_used": False, "skip_reason": "provider_unavailable", "strategy_count": len(rows), "selected_style_example_ids": []})
        context: dict[str, Any] = {"decision_run_id": decision_run_id}
        stage = LLMFailureStage.UNKNOWN.value
        profile_getter = getattr(self.llm_provider, "task_profile", None)
        profile = profile_getter(LLMTask.STRATEGY_EXPRESSION) if callable(profile_getter) else None
        try:
            payload = self._payload(rows)
            raw = generate_json_sync(self.llm_provider, SYSTEM_PROMPT, payload, task=LLMTask.STRATEGY_EXPRESSION, request_context=context)
            try:
                typed = StrategyExpressionLLMResponse.model_validate(raw)
            except Exception as exc:
                stage = LLMFailureStage.SCHEMA_VALIDATION.value
                raise ValueError("strategy_expression_schema_validation") from exc
            try:
                self._validate(typed, rows)
            except ValueError as exc:
                stage = (LLMFailureStage.GROUNDING.value if any(token in str(exc) for token in ("number", "entity", "reason", "terminology")) else LLMFailureStage.BUSINESS_VALIDATION.value)
                raise
            rendered = {item.strategy: BriefStrategyPresentation(headline=item.headline, summary=item.summary, reason_messages=item.reason_messages) for item in typed.strategies}
            metadata = context.get("openrouter_metadata", {})
            return StrategyExpressionResult([row.model_copy(update={"presentation": rendered[row.strategy]}) for row in rows], self._diagnostics(True, "success", "llm", False, None, None, profile, metadata, rows, payload))
        except Exception as exc:
            details = getattr(exc, "details", {})
            if stage == LLMFailureStage.UNKNOWN.value and isinstance(details, dict): stage = str(details.get("failure_stage") or stage)
            metadata = context.get("openrouter_metadata", {})
            return StrategyExpressionResult(fallback, self._diagnostics(True, "fallback", "deterministic_fallback", True, stage, str(exc), profile, metadata, rows, locals().get("payload")))

    @staticmethod
    def _diagnostics(attempted, status, source, fallback_used, stage, error, profile, metadata, rows, payload):
        metadata = metadata if isinstance(metadata, dict) else {}
        return {
            "attempted": attempted, "status": status, "source": source, "fallback_used": fallback_used,
            "failure_stage": stage, "error_message": " ".join(str(error).split())[:240] if error else None,
            "provider": metadata.get("resolved_provider"), "requested_model": getattr(profile, "model", None),
            "resolved_model": metadata.get("resolved_model"), "finish_reason": metadata.get("finish_reason"),
            "strategy_count": len(rows), "selected_style_example_ids": [x["example_id"] for x in (payload or {}).get("style_examples", [])],
            "prompt_tokens": metadata.get("prompt_tokens"), "completion_tokens": metadata.get("completion_tokens"),
            "total_tokens": metadata.get("total_tokens"), "reasoning_tokens": metadata.get("reasoning_tokens"), "cost": metadata.get("cost"),
        }

    def _payload(self, rows: list[BriefStrategyEvaluation]) -> dict[str, Any]:
        strategies = []
        for row in rows:
            reasons = []
            for reason in row.reasons:
                display = {key: value for key, value in ((key, _display(key, value)) for key, value in reason.values.items()) if value is not None}
                reasons.append({"kind": reason.kind, "code": reason.code, "values": reason.values, "display_values": display})
            strategies.append({"strategy": row.strategy, "label": row.label, "status": row.status, "selected": row.selected, "feasible": row.feasible, "purchase_cost": row.purchase_cost, "reason_status": row.reason_status, "reasons": reasons})
        cases = list(dict.fromkeys(["unavailable" if row.reason_status == "unavailable" else row.status for row in rows]))[:2]
        examples = [{"example_id": f"strategy-{case}", "template": "<STRATEGY>: dien dat ngan gon theo facts duoc cap.", "negative": False} for case in cases]
        return {"task": "strategy_expression", "communication_plan": strategy_communication_plan(rows), "strategies": strategies, "style_examples": examples}

    def _validate(self, typed: StrategyExpressionLLMResponse, rows: list[BriefStrategyEvaluation]) -> None:
        expected = {row.strategy for row in rows}; actual = [item.strategy for item in typed.strategies]
        if len(actual) != len(expected) or set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("strategy_expression_strategy_set")
        by_strategy = {row.strategy: row for row in rows}
        for item in typed.strategies:
            row = by_strategy[item.strategy]; text = " ".join([item.headline, item.summary, *item.reason_messages]).lower()
            if any(term in text for term in _FORBIDDEN): raise ValueError("strategy_expression_forbidden_terminology")
            labels = {candidate.label.lower() for candidate in rows}
            mentioned = {label for label in ("tiết kiệm", "tinh gọn", "cân bằng", "an toàn") if label in text}
            if any(label not in labels for label in mentioned): raise ValueError("strategy_expression_unauthorized_entity")
            allowed_numbers = {value for reason in row.reasons for value in (_display(key, raw) for key, raw in reason.values.items()) if value}
            scrubbed = text
            for value in allowed_numbers: scrubbed = scrubbed.replace(value.lower(), "")
            if re.search(r"\d", scrubbed): raise ValueError("strategy_expression_unauthorized_number")
            self._validate_semantics(text, row)

    @staticmethod
    def _validate_semantics(text: str, row: BriefStrategyEvaluation) -> None:
        if row.status == "selected" and any(x in text for x in ("an toàn hơn", "rủi ro thấp", "ít rủi ro", "dịch vụ tốt", "chất lượng cao")):
            raise ValueError("strategy_expression_unsupported_selected_claim")
        if row.status == "feasible_not_selected" and any(x in text for x in ("thất bại", "không khả thi", "bị từ chối", "không đáp ứng điều kiện bắt buộc")): raise ValueError("strategy_expression_feasible_as_failure")
        if row.status in {"technical_failure", "not_evaluated"} and any(x in text for x in ("không khả thi", "vi phạm điều kiện", "không đạt ngân sách", "không đạt mức")): raise ValueError("strategy_expression_technical_as_business_failure")
        if row.status == "selected" and row.reason_status == "unavailable" and any(x in text for x in ("thấp nhất", "chi phí thấp")): raise ValueError("strategy_expression_unavailable_as_lowest_cost")
        allowed = {theme for reason in row.reasons for theme in _THEMES.get(reason.code, ())}
        for code, themes in _THEMES.items():
            if code not in {reason.code for reason in row.reasons} and any(theme in text for theme in themes):
                raise ValueError("strategy_expression_unauthorized_reason")
