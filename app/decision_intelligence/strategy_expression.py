"""Optional, grounded wording for the already-authoritative strategy brief."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Any

from app.decision_intelligence.contracts import (
    BriefStrategyEvaluation, BriefStrategyPresentation, StrategyExpressionLLMResponse,
)
from app.decision_intelligence.display import date_display, percentage_display, purchase_cost_display, vi_number
from app.llm.runtime import generate_json_sync
from app.llm.tasks import LLMFailureStage, LLMTask

SYSTEM_PROMPT = """Backend đã xác định trạng thái, kết quả chọn, lý do và giá trị được phép. Bạn chỉ diễn đạt các facts này thành tiếng Việt ngắn gọn; không tính toán, suy luận, đổi trạng thái, thêm lý do hay so sánh ngoài lý do đã được cấp.
COMMUNICATION_PLAN là bắt buộc. Chỉ được dùng số trong display_values và phải sao chép nguyên văn; không dùng số raw, không đổi độ chính xác, đơn vị hoặc cách biểu diễn, không tự tạo số lượng, phần trăm, chênh lệch hay thứ hạng.
STYLE_EXAMPLES chỉ minh họa văn phong, không phải facts. Trả về JSON đúng schema. Không dùng critic, solver, candidate, objective, M4, FEFO, CVAR, Monte Carlo, semantic evidence, selection proof, grounding, OpenRouter, Qwen, lexical ordering hoặc tỷ lệ lấp kho.
"""

_FORBIDDEN = ("critic", "solver", "candidate", "objective", "m4", "fefo", "cvar", "monte carlo", "semantic evidence", "selection proof", "grounding", "openrouter", "qwen", "lexical ordering", "tỷ lệ lấp kho", "ty le lap kho")
_THEMES = {
    "BUDGET": ("ngân sách",), "SERVICE_LEVEL_REQUIREMENT": ("thấp hơn mức yêu cầu",),
    "HIGHER_PURCHASE_COST_THAN_SELECTED": ("cao hơn phương án được chọn", "chênh lệch"), "LOWEST_EXACT_VALID_CANDIDATE_COST": ("thấp nhất",),
    "STRATEGY_NAME_TIEBREAK": ("cùng chi phí", "phân định"), "LEAD_TIME": ("giao hàng", "kịp thời điểm", "ngày nhận"),
    "MOQ": ("mức đặt tối thiểu",), "PACK_SIZE": ("quy cách đóng gói",), "SUPPLIER_UNAVAILABLE": ("nhà cung cấp không thể đáp ứng", "nhà cung cấp không khả dụng"),
    "ORDER_CUTOFF": ("thời điểm có thể đặt",), "SUPPLIER_MAX_QUANTITY": ("giới hạn số lượng",),
    "SUPPLIER_MAX_COST": ("giới hạn giá trị đơn hàng",), "CAPACITY_CONSEQUENCE": ("giới hạn tồn kho",),
    "UNKNOWN_EXPIRY": ("hạn sử dụng",), "EXACT_SIMULATION_SAFETY_FLOOR": ("tối thiểu sau khi kế hoạch",),
    "RISK_CONSTRAINT_VIOLATION": ("giới hạn rủi ro",), "EVALUATION_TECHNICAL_FAILURE": ("lỗi kỹ thuật",),
    "SELECTION_REASON_UNAVAILABLE": ("chưa đủ dữ liệu để xác nhận lý do",), "EVALUATION_OUTCOME_UNAVAILABLE": ("chưa có đủ kết quả",),
}
_PUBLIC_STRATEGY_LABELS = ("tiết kiệm", "cân bằng", "an toàn")
_INVENTED_STRATEGY_LABELS = ("siêu an toàn", "tiết kiệm tối đa", "trung lập")
# These are deliberately narrow, manager-facing claims whose meaning is stronger
# than the generic nouns they contain.  Keep ordinary words such as "nhu cầu"
# and "nhà cung cấp" outside this table: known vocabulary is not a reason.
_REASON_AUTHORIZATION_PHRASES = {
    "RISK_CONSTRAINT_VIOLATION": ("rủi ro tồn kho thấp hơn", "rủi ro thấp hơn", "ít rủi ro hơn"),
    "SERVICE_LEVEL_REQUIREMENT": ("mức đáp ứng nhu cầu thấp hơn yêu cầu",),
    "SUPPLIER_UNAVAILABLE": ("nhà cung cấp không thể đáp ứng đơn hàng", "nhà cung cấp không khả dụng"),
    "CAPACITY_CONSEQUENCE": ("vượt sức chứa kho", "kho bị quá tải"),
}


def _numeric_value(text: str) -> Decimal | None:
    """Diagnostic-only normalization; it never authorizes a rewritten value."""
    match = re.fullmatch(r"\s*(\d[\d.,]*)\s*(triệu đồng|nghìn đồng|đồng|%)?\s*", text.lower())
    if not match:
        return None
    number, unit = match.groups()
    try:
        if unit in {"triệu đồng", "nghìn đồng", "%"}:
            value = Decimal(number.replace(".", "").replace(",", "."))
            return value * {"triệu đồng": Decimal("1000000"), "nghìn đồng": Decimal("1000"), "%": Decimal("0.01")}[unit]
        return Decimal(number.replace(".", "").replace(",", ""))
    except InvalidOperation:
        return None


def _display(key: str, value: object) -> str | None:
    if key.endswith("date") and isinstance(value, str):
        return date_display(value)
    if "rate" in key and isinstance(value, (int, float)):
        return percentage_display(value)
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
        result = {
            "attempted": attempted, "status": status, "source": source, "fallback_used": fallback_used,
            "failure_stage": stage, "error_message": " ".join(str(error).split())[:240] if error else None,
            "provider": metadata.get("resolved_provider"), "requested_model": getattr(profile, "model", None),
            "resolved_model": metadata.get("resolved_model"), "finish_reason": metadata.get("finish_reason"),
            "strategy_count": len(rows), "selected_style_example_ids": [x["example_id"] for x in (payload or {}).get("style_examples", [])],
            "prompt_tokens": metadata.get("prompt_tokens"), "completion_tokens": metadata.get("completion_tokens"),
            "total_tokens": metadata.get("total_tokens"), "reasoning_tokens": metadata.get("reasoning_tokens"), "cost": metadata.get("cost"),
        }
        if error and str(error).startswith("strategy_expression_unauthorized_"):
            kind, strategy, field, value = str(error).split(":", 3)
            row = next((item for item in rows if item.strategy == strategy), None)
            base = {"offending_strategy": strategy, "offending_field": field}
            if kind == "strategy_expression_unauthorized_number":
                mentions = [item.strip() for item in value.split("|") if item.strip()]
                authorized = [item for reason in (row.reasons if row else []) for item in (_display(key, raw) for key, raw in reason.values.items()) if item]
                authorized_values = {_numeric_value(item) for item in authorized}
                failure_kind = "NUMERIC_REPRESENTATION_CHANGE" if any(
                    _numeric_value(mention) is not None and _numeric_value(mention) in authorized_values
                    for mention in mentions
                ) else "NUMERIC_HALLUCINATION"
                result["details"] = {**base, "offending_numeric_mentions": mentions,
                                     "authorized_numeric_mentions": authorized, "numeric_failure_kind": failure_kind}
            elif kind == "strategy_expression_unauthorized_reason":
                result["details"] = {**base, "detected_phrase": value,
                                     "authorized_reason_codes": [reason.code for reason in (row.reasons if row else [])]}
            elif kind == "strategy_expression_unauthorized_entity":
                result["details"] = {**base, "offending_entity": value,
                                     "authorized_entities": [row.label] if row else []}
        return result

    def _payload(self, rows: list[BriefStrategyEvaluation]) -> dict[str, Any]:
        strategies = []
        for row in rows:
            reasons = []
            for reason in row.reasons:
                display = {key: value for key, value in ((key, _display(key, value)) for key, value in reason.values.items()) if value is not None}
                reasons.append({"kind": reason.kind, "code": reason.code, "display_values": display})
            strategies.append({"strategy": row.strategy, "label": row.label, "status": row.status, "selected": row.selected, "feasible": row.feasible, "reason_status": row.reason_status, "reasons": reasons})
        cases = list(dict.fromkeys(["unavailable" if row.reason_status == "unavailable" else row.status for row in rows]))[:2]
        examples = [{"example_id": f"strategy-{case}", "template": "<STRATEGY>: dien dat ngan gon theo facts duoc cap.", "negative": False} for case in cases]
        return {"task": "strategy_expression", "communication_plan": strategy_communication_plan(rows), "strategies": strategies, "style_examples": examples}

    def _validate(self, typed: StrategyExpressionLLMResponse, rows: list[BriefStrategyEvaluation]) -> None:
        expected = {row.strategy for row in rows}; actual = [item.strategy for item in typed.strategies]
        if len(actual) != len(expected) or set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("strategy_expression_strategy_set")
        by_strategy = {row.strategy: row for row in rows}
        for item in typed.strategies:
            row = by_strategy[item.strategy]
            fields = [("headline", item.headline), ("summary", item.summary), *[(f"reason_messages[{index}]", value) for index, value in enumerate(item.reason_messages)]]
            text = " ".join(value for _, value in fields).lower()
            if any(term in text for term in _FORBIDDEN): raise ValueError("strategy_expression_forbidden_terminology")
            allowed_numbers = {value for reason in row.reasons for value in (_display(key, raw) for key, raw in reason.values.items()) if value}
            # IS-4.4 deterministically states the count of already-authorized
            # public reasons for rejected strategies; keep that frozen wording
            # valid without exposing any raw business metric.
            if row.status == "rejected": allowed_numbers.add(str(len(row.reasons)))
            for field, value in fields:
                scrubbed = value.lower()
                for allowed in allowed_numbers: scrubbed = scrubbed.replace(allowed.lower(), "")
                if re.search(r"\d", scrubbed):
                    mentions = re.findall(r"\d[\d.,]*\s*(?:triệu đồng|nghìn đồng|đồng|%)?", scrubbed)
                    raise ValueError(f"strategy_expression_unauthorized_number:{row.strategy}:{field}:{'|'.join(mentions)}")
            self._validate_entities(fields, row)
            self._validate_reason_authorization(fields, row)
            self._validate_semantics(text, row)

    @staticmethod
    def _validate_entities(fields: list[tuple[str, str]], row: BriefStrategyEvaluation) -> None:
        """A strategy may name itself, never another evaluated or invented strategy."""
        own_label = row.label.casefold()
        for field, value in fields:
            lowered = value.casefold()
            for invented in _INVENTED_STRATEGY_LABELS:
                if invented in lowered:
                    raise ValueError(f"strategy_expression_unauthorized_entity:{row.strategy}:{field}:{invented}")
            for label in _PUBLIC_STRATEGY_LABELS:
                if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", lowered) and label != own_label:
                    raise ValueError(f"strategy_expression_unauthorized_entity:{row.strategy}:{field}:{label}")

    @staticmethod
    def _validate_reason_authorization(fields: list[tuple[str, str]], row: BriefStrategyEvaluation) -> None:
        """Reason phrases are authority-bound per strategy, not merely known vocabulary."""
        authorized = {reason.code for reason in row.reasons}
        for field, value in fields:
            lowered = value.casefold()
            for code in set(_THEMES) | set(_REASON_AUTHORIZATION_PHRASES):
                phrases = (*_THEMES.get(code, ()), *_REASON_AUTHORIZATION_PHRASES.get(code, ()))
                if code in authorized:
                    continue
                phrase = next((phrase for phrase in phrases if phrase in lowered), None)
                if phrase:
                    raise ValueError(f"strategy_expression_unauthorized_reason:{row.strategy}:{field}:{phrase}")

    @staticmethod
    def _validate_semantics(text: str, row: BriefStrategyEvaluation) -> None:
        codes = {reason.code for reason in row.reasons}
        if row.status == "selected":
            if "được chọn" not in text: raise ValueError("strategy_expression_missing_selected_semantics")
            if row.reason_status == "unavailable":
                if "chưa đủ dữ liệu" not in text: raise ValueError("strategy_expression_missing_selection_uncertainty")
            elif "LOWEST_EXACT_VALID_CANDIDATE_COST" in codes and "thấp nhất" not in text:
                raise ValueError("strategy_expression_missing_selected_reason")
        if row.status == "feasible_not_selected":
            if not any(x in text for x in ("hợp lệ", "đáp ứng các điều kiện")): raise ValueError("strategy_expression_missing_feasible_semantics")
            if not any(x in text for x in ("không được chọn", "cùng chi phí nhập với phương án được chọn")): raise ValueError("strategy_expression_missing_not_selected_semantics")
            if "HIGHER_PURCHASE_COST_THAN_SELECTED" in codes and not any(x in text for x in ("cao hơn", "chênh lệch")): raise ValueError("strategy_expression_missing_non_selection_reason")
        if row.status == "rejected" and not any(x in text for x in ("không đáp ứng", "điều kiện bắt buộc")):
            raise ValueError("strategy_expression_missing_rejected_semantics")
        if row.status == "technical_failure" and not any(x in text for x in ("chưa thể đánh giá", "lỗi kỹ thuật", "chưa thể kết luận")):
            raise ValueError("strategy_expression_missing_evaluation_uncertainty")
        if row.status == "not_evaluated" and not (any(x in text for x in ("chưa có đủ kết quả", "chưa đủ dữ liệu")) and "kết luận" in text):
            raise ValueError("strategy_expression_missing_evaluation_uncertainty")
        if "STRATEGY_NAME_TIEBREAK" in codes and not ("phân định" in text and "chi phí bằng nhau" in text):
            raise ValueError("strategy_expression_missing_tiebreak_semantics")
        if row.status == "selected" and any(x in text for x in ("an toàn hơn", "rủi ro thấp", "ít rủi ro", "dịch vụ tốt", "chất lượng cao")):
            raise ValueError("strategy_expression_unsupported_selected_claim")
        if row.status == "feasible_not_selected" and any(x in text for x in ("thất bại", "không khả thi", "bị từ chối", "không đáp ứng điều kiện bắt buộc")): raise ValueError("strategy_expression_feasible_as_failure")
        if row.status in {"technical_failure", "not_evaluated"} and any(x in text for x in ("không khả thi", "vi phạm điều kiện", "không đạt ngân sách", "không đạt mức")): raise ValueError("strategy_expression_technical_as_business_failure")
        if row.status == "selected" and row.reason_status == "unavailable" and any(x in text for x in ("thấp nhất", "chi phí thấp")): raise ValueError("strategy_expression_unavailable_as_lowest_cost")
        allowed = {theme for reason in row.reasons for theme in _THEMES.get(reason.code, ())}
        for code, themes in _THEMES.items():
            if code not in {reason.code for reason in row.reasons} and any(theme in text for theme in themes):
                raise ValueError("strategy_expression_unauthorized_reason")
