"""Optional, grounded wording for the already-authoritative strategy brief."""
from __future__ import annotations

import re
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


class StrategyExpressionProvider:
    def __init__(self, llm_provider, settings):
        self.llm_provider, self.settings = llm_provider, settings
        self.last_diagnostics: dict[str, Any] = {}

    def express(self, rows: list[BriefStrategyEvaluation], decision_run_id: str | None = None) -> list[BriefStrategyEvaluation]:
        fallback = rows
        if not rows or not self.llm_provider or not getattr(self.llm_provider, "available", False):
            self.last_diagnostics = {"llm_attempted": False, "llm_status": "disabled" if rows else "not_applicable"}
            return fallback
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
            self.last_diagnostics = {"llm_attempted": True, "llm_status": "success", "provider": metadata.get("resolved_provider") if isinstance(metadata, dict) else None, "requested_model": getattr(profile, "model", None), "resolved_model": metadata.get("resolved_model") if isinstance(metadata, dict) else None, "failure_stage": None, "selected_style_example_ids": [x["example_id"] for x in payload["style_examples"]], "metadata": metadata}
            return [row.model_copy(update={"presentation": rendered[row.strategy]}) for row in rows]
        except Exception as exc:
            details = getattr(exc, "details", {})
            if stage == LLMFailureStage.UNKNOWN.value and isinstance(details, dict): stage = str(details.get("failure_stage") or stage)
            metadata = context.get("openrouter_metadata", {})
            self.last_diagnostics = {"llm_attempted": True, "llm_status": "fallback", "provider": metadata.get("resolved_provider") if isinstance(metadata, dict) else None, "requested_model": getattr(profile, "model", None), "resolved_model": metadata.get("resolved_model") if isinstance(metadata, dict) else None, "failure_stage": stage, "error_message": str(exc), "metadata": metadata}
            return fallback

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
