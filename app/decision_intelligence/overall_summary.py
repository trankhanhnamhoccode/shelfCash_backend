"""One-time, grounded overall summaries for persisted Decision Runs."""
from __future__ import annotations

import logging
from typing import Any

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.communication_plan import summary_communication_plan
from app.decision_intelligence.display import add_numeric_display_contract, purchase_cost_display
from app.decision_intelligence.strategy_comparison import strategy_label
from app.decision_intelligence.contracts import (
    AssistantSummary,
    DecisionBriefFacts,
    DecisionOverallSummaryLLMResponse,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import (
    SemanticFact,
    SemanticFactClassification,
    SemanticFactScope,
)
from app.decision_intelligence.style_examples import retrieve_style_examples
from app.llm.tasks import LLMFailureStage, LLMTask
from app.llm.runtime import generate_json_sync

logger = logging.getLogger("shelfcash.overall_summary")


SYSTEM_PROMPT = """Bạn là lớp diễn đạt cuối cùng cho quản lý cửa hàng. Backend đã hoàn tất dự báo, mô phỏng và quyết định; bạn chỉ diễn đạt facts được cung cấp, không tính toán hay tự quyết định.

COMMUNICATION_PLAN là authority: nói decision trước, chỉ dùng main_risk khi có, limitation khi có, và supporting chỉ để hỗ trợ. Không thay thế role backend đã chọn.

Chỉ dùng EVIDENCE được cung cấp. Sao chép số/ngày đúng từ display_values hoặc allowed_numeric_mentions; không tính lại, làm tròn, đổi đơn vị hoặc đổi dấu phân cách. STYLE_EXAMPLES chỉ là văn phong. OBSERVATION và DERIVED không được dùng ngôn ngữ nguyên nhân; chỉ CAUSAL hoặc PROCUREMENT_REASON được phép. Dùng “tỷ lệ đáp ứng nhu cầu”, không dùng “tỷ lệ lấp kho”. CONSERVATIVE_DESIGN phải nói rõ là kịch bản nhu cầu bảo thủ; STRESS phải nói rõ là kịch bản kiểm tra sức chịu đựng; CAPACITY_NOT_EVALUATED chỉ là chưa đánh giá đầy đủ.

Trả về đúng một JSON object: headline và summary là DecisionNarrativeClaim; key_points là tối đa 3 DecisionNarrativeClaim; warning_summary là DecisionNarrativeClaim hoặc null. Mỗi DecisionNarrativeClaim gồm type, text, evidence_ids; type phải đúng semantic type của ít nhất một evidence_id. Không có mảng claims ở cấp cao nhất, không có used_evidence_ids ở cấp cao nhất, không markdown."""


def _strategy_label(strategy: str | None) -> str | None:
    return strategy_label(strategy) if strategy else None


class OverallSummaryProvider:
    """Uses the existing gateway and narrative guard; never owns business math."""

    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings
        self._adapter = ShelfCashDecisionIntelligenceAdapter()
        self._guard = DecisionNarrativeProvider(None, settings)

    def deterministic_fallback(
        self,
        brief: DecisionBriefFacts,
        facts: list[SemanticFact],
    ) -> AssistantSummary:
        overview = next((fact for fact in facts if fact.fact_type == "PLAN_OVERVIEW"), None)
        limitations = [
            fact for fact in facts
            if fact.classification is SemanticFactClassification.LIMITATION
        ]
        risk_signals = [
            fact for fact in facts
            if fact.classification is SemanticFactClassification.RISK_SIGNAL
        ]
        conservative_risks = [
            fact for fact in risk_signals
            if fact.values.get("basis_kind") == "conservative_design_scenario"
        ]
        stress_risks = [
            fact for fact in risk_signals
            if fact.fact_type.startswith("STRESS_")
        ]
        selected_plan_risks = [
            fact for fact in risk_signals
            if fact not in conservative_risks and fact not in stress_risks
        ]
        if not brief.recommendation.available:
            return AssistantSummary(
                headline="Ch\u01b0a c\u00f3 k\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng kh\u1ea3 thi",
                summary=(
                    "ShelfCash ch\u01b0a t\u00ecm \u0111\u01b0\u1ee3c k\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng kh\u1ea3 thi "
                    "trong c\u00e1c \u0111i\u1ec1u ki\u1ec7n hi\u1ec7n t\u1ea1i."
                ),
                key_points=[],
                warning_summary=(
                    "M\u1ed9t s\u1ed1 \u0111i\u1ec1u ki\u1ec7n ho\u1eb7c ch\u1ec9 s\u1ed1 c\u1ea7n \u0111\u01b0\u1ee3c xem x\u00e9t th\u00eam."
                    if limitations else None
                ),
                source="deterministic_fallback",
                grounded=True,
            )

        count = int(overview.values.get("ordered_ingredient_count") or 0) if overview else len(brief.procurement_rows)
        horizon = int(overview.values.get("horizon_days") or 0) if overview else brief.forecast.horizon_days
        summary = f"ShelfCash \u0111\u1ec1 xu\u1ea5t nh\u1eadp {count} nguy\u00ean li\u1ec7u"
        cost = overview.values.get("total_purchase_cost") if overview else None
        if isinstance(cost, (int, float)):
            summary += f" v\u1edbi t\u1ed5ng chi ph\u00ed d\u1ef1 ki\u1ebfn {purchase_cost_display(cost)}"
        if horizon:
            summary += f" cho {horizon} ng\u00e0y t\u1edbi"
        summary += "."
        strategy = _strategy_label(brief.recommendation.strategy)
        points = [
            f"K\u1ebf ho\u1ea1ch hi\u1ec7n t\u1ea1i s\u1eed d\u1ee5ng chi\u1ebfn l\u01b0\u1ee3c {strategy}."
        ] if strategy else []
        if conservative_risks:
            points.append("Trong kịch bản nhu cầu bảo thủ, mô phỏng ghi nhận tín hiệu thiếu hàng cần theo dõi.")
        elif stress_risks:
            points.append("Một số kịch bản kiểm tra ghi nhận tín hiệu thiếu hàng hoặc vượt sức chứa.")
        elif selected_plan_risks:
            points.append("Kế hoạch hiện tại có một tín hiệu rủi ro vận hành cần theo dõi.")
        return AssistantSummary(
            headline=(
                f"K\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng {horizon} ng\u00e0y"
                if horizon else "K\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng"
            ),
            summary=summary,
            key_points=points[:3],
            warning_summary=(
                "M\u1ed9t s\u1ed1 ch\u1ec9 s\u1ed1 r\u1ee7i ro ch\u01b0a \u0111\u1ee7 d\u1eef li\u1ec7u \u0111\u1ec3 \u0111\u00e1nh gi\u00e1 \u0111\u1ea7y \u0111\u1ee7."
                if limitations else None
            ),
            source="deterministic_fallback",
            grounded=True,
        )

    def summarize(self, brief: DecisionBriefFacts, facts: list[SemanticFact]) -> AssistantSummary:
        fallback = self.deterministic_fallback(brief, facts)
        if not brief.recommendation.available or not self.llm_provider or not self.llm_provider.available:
            return fallback
        request_context: dict[str, Any] = {"decision_run_id": brief.decision_run_id}
        failure_stage = LLMFailureStage.UNKNOWN.value
        try:
            evidence, structured = self._context(brief, facts)
            if not structured:
                return fallback
            plan = summary_communication_plan(structured)
            selected_ids = set(plan.evidence_ids)
            selected = [item for item in structured if item["evidence_id"] in selected_ids]
            presentation_roles = self._presentation_roles(plan, selected)
            main_risk_provenance = presentation_roles["main_risk"]["presentation_provenance"]
            summary_case = (
                "NO_FEASIBLE" if not brief.recommendation.available else
                f"{main_risk_provenance}_RISK" if plan.main_risk else
                "WITH_LIMITATION" if plan.limitation else "FEASIBLE"
            )
            payload = {
                "language": "vi", "communication_plan": plan.as_payload(presentation_roles=presentation_roles), "evidence": selected,
                "style_examples": retrieve_style_examples(
                    task="overall_summary", intent="SUMMARY", case=summary_case,
                    detail_level="simple",
                ),
            }
            logger.info(
                "overall_summary_communication_plan decision_run_id=%s decision=%s main_risk=%s limitation=%s supporting=%s",
                brief.decision_run_id, plan.decision, plan.main_risk, plan.limitation, plan.supporting,
            )
            raw = self._run_gateway(payload, request_context)
            try:
                typed = DecisionOverallSummaryLLMResponse.model_validate(raw)
            except Exception as exc:
                failure_stage = LLMFailureStage.SCHEMA_VALIDATION.value
                raise ValueError("overall_summary_schema_validation_failed") from exc
            try:
                self._validate_expression(typed, set(plan.evidence_ids), selected)
            except ValueError as exc:
                # The response referenced an ID outside the authoritative
                # CommunicationPlan.  This is grounding/authorization, not an
                # unknown provider failure.
                if str(exc) == "overall_summary_unauthorized_evidence":
                    failure_stage = LLMFailureStage.GROUNDING.value
                raise
            claims = [typed.headline, typed.summary, *typed.key_points]
            if typed.warning_summary is not None:
                claims.append(typed.warning_summary)
            used_evidence_ids = list(dict.fromkeys(
                evidence_id for claim in claims for evidence_id in claim.evidence_ids
            ))
            guard_raw = {
                "answer": typed.summary.text,
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "used_evidence_ids": used_evidence_ids,
            }
            # Reuse the established claim/evidence/numeric/entity/causal guard.
            try:
                self._guard._guard(
                    guard_raw, selected, evidence.items, brief, "vi", "simple", "OVERALL_SUMMARY",
                )
            except Exception:
                failure_stage = LLMFailureStage.GROUNDING.value
                raise
            return AssistantSummary(
                headline=typed.headline.text,
                summary=typed.summary.text,
                key_points=[item.text for item in typed.key_points],
                warning_summary=typed.warning_summary.text if typed.warning_summary else None,
                source="llm",
                grounded=True,
                raw_response=request_context.get("openrouter_raw_content", raw),
                llm_diagnostics={
                    "status": "success",
                    "failure_stage": None,
                    "communication_decision_role": "primary_decision",
                    "communication_main_risk_role": "main_risk" if plan.main_risk else None,
                    "communication_limitation_role": "limitation" if plan.limitation else None,
                    "authorized_evidence_ids": plan.evidence_ids,
                    "selected_style_example_ids": [item["example_id"] for item in payload["style_examples"]],
                    "causal_allowed": False,
                    "dedup_validation_status": "passed",
                    "metadata": request_context.get("openrouter_metadata", {}),
                },
            )
        except Exception as exc:
            details = getattr(exc, "details", {})
            if failure_stage == LLMFailureStage.UNKNOWN.value and isinstance(details, dict):
                failure_stage = str(details.get("failure_stage") or failure_stage)
            profile_getter = getattr(self.llm_provider, "task_profile", None)
            profile = profile_getter(LLMTask.PLAN_SUMMARY) if callable(profile_getter) else None
            metadata = request_context.get("openrouter_metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            logger.warning(
                "overall_summary_fallback decision_run_id=%s task=%s configured_model=%s resolved_provider=%s failure_stage=%s reason=%s",
                brief.decision_run_id, LLMTask.PLAN_SUMMARY.value,
                getattr(profile, "model", None), metadata.get("resolved_provider"),
                failure_stage, type(exc).__name__,
            )
            raw_response = request_context.get("openrouter_raw_content")
            if raw_response is None:
                raw_response = request_context.get("openrouter_raw_response")
            return fallback.model_copy(update={
                "raw_response": raw_response,
                "llm_diagnostics": {
                    "status": "failed",
                    "failure_stage": failure_stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "http_status": getattr(exc, "http_status", None),
                    "details": details if isinstance(details, dict) else {},
                    "metadata": metadata,
                },
            })

    def _run_gateway(self, payload: dict[str, Any], request_context: dict[str, Any]) -> dict[str, Any]:
        return generate_json_sync(
            self.llm_provider, SYSTEM_PROMPT, payload,
            task=LLMTask.PLAN_SUMMARY, request_context=request_context,
        )

    @staticmethod
    def _presentation_roles(plan, selected: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Internal model instructions derived only from selected structured evidence."""
        by_id = {item["evidence_id"]: item for item in selected}

        def role(ids: list[str], provenance: str, framing: str) -> dict[str, Any]:
            return {
                "evidence_ids": ids,
                "presentation_provenance": provenance,
                "required_framing": framing,
            }

        risk = by_id.get(plan.main_risk[0]) if plan.main_risk else None
        risk_provenance = (
            "CONSERVATIVE_DESIGN" if risk and risk.get("basis_kind") == "conservative_design_scenario" else
            "STRESS" if risk and str(risk.get("type", "")).startswith("STRESS_") else
            "SELECTED_PLAN"
        )
        risk_framing = {
            "CONSERVATIVE_DESIGN": "Explicitly say this is a conservative demand scenario (kịch bản nhu cầu bảo thủ), not the current plan.",
            "STRESS": "Explicitly say this is a stress/adverse testing scenario (kịch bản kiểm tra sức chịu đựng), not the current plan.",
            "SELECTED_PLAN": "Selected/current-plan wording is allowed only for this risk.",
        }[risk_provenance]
        return {
            "decision": role(plan.decision, "SELECTED_PLAN", "State the selected recommendation first."),
            "main_risk": role(plan.main_risk, risk_provenance, risk_framing),
            "limitation": role(plan.limitation, "LIMITATION", "Describe incomplete evaluation only; do not turn it into an operational failure."),
            "supporting": role(plan.supporting, "SUPPORTING", "Use only as non-authoritative support."),
        }

    @staticmethod
    def _validate_expression(typed: DecisionOverallSummaryLLMResponse, authorized: set[str], selected: list[dict[str, Any]]) -> None:
        """Small deterministic anti-duplication and plan-authority boundary."""
        claims = [typed.headline, typed.summary, *typed.key_points]
        if typed.warning_summary is not None:
            claims.append(typed.warning_summary)
        claimed_ids = {evidence_id for claim in claims for evidence_id in claim.evidence_ids}
        if not claimed_ids <= authorized:
            raise ValueError("overall_summary_unauthorized_evidence")
        texts = [claim.text.strip().lower() for claim in [*typed.key_points, *([typed.warning_summary] if typed.warning_summary else [])]]
        if len(texts) != len(set(texts)):
            raise ValueError("overall_summary_duplicate_output")
        summary = typed.summary.text.strip().lower()
        if any(point and point == summary for point in texts):
            raise ValueError("overall_summary_duplicate_output")
        all_text = " ".join([typed.headline.text, typed.summary.text, *[item.text for item in typed.key_points], *( [typed.warning_summary.text] if typed.warning_summary else [])]).lower()
        if "tỷ lệ lấp kho" in all_text:
            raise ValueError("overall_summary_invalid_fill_rate_terminology")
        by_id = {item["evidence_id"]: item for item in selected}
        for claim in claims:
            cited = [by_id[item] for item in claim.evidence_ids if item in by_id]
            text = claim.text.lower()
            if any(item.get("basis_kind") == "conservative_design_scenario" for item in cited):
                if "kế hoạch hiện tại" in text or "phương án được chọn" in text:
                    raise ValueError("overall_summary_conservative_mislabeled_as_selected")
                if "kịch bản" not in text:
                    raise ValueError("overall_summary_conservative_provenance_missing")
            if any(str(item.get("type", "")).startswith("STRESS_") for item in cited):
                if "kế hoạch hiện tại" in text or "phương án được chọn" in text:
                    raise ValueError("overall_summary_stress_mislabeled_as_selected")
                if not any(token in text for token in ("kịch bản", "kiểm tra", "sức chịu đựng", "bất lợi")):
                    raise ValueError("overall_summary_stress_provenance_missing")
            if any(item.get("type") == "CAPACITY_NOT_EVALUATED" for item in cited):
                # This limitation states that store-level capacity was not
                # evaluated; it is not evidence of an exceeded capacity.
                exceeded_capacity_markers = (
                    "vượt công suất", "quá tải", "vượt sức chứa",
                    "khả năng lưu trữ đã bị vượt", "warehouse over capacity",
                    "storage exceeded", "capacity exceeded",
                )
                if any(marker in text for marker in exceeded_capacity_markers):
                    raise ValueError("overall_summary_capacity_not_evaluated_mislabeled_as_exceeded")

    def _context(self, brief: DecisionBriefFacts, facts: list[SemanticFact]):
        evidence = self._adapter._evidence(brief, semantic_facts=facts)
        evidence_by_fact_id = {
            str(item.payload.get("semantic_fact_id")): item
            for item in evidence.items
            if item.evidence_type.startswith("semantic_") and item.payload.get("semantic_fact_id")
        }
        records: list[dict[str, Any]] = []
        for fact in self._select_facts(facts):
            item = evidence_by_fact_id.get(fact.fact_id)
            if item is None:
                continue
            record = {
                "evidence_id": item.evidence_id,
                "evidence_ids": [item.evidence_id],
                "type": fact.fact_type,
                "classification": fact.classification.value,
                **fact.entities,
                **fact.values,
            }
            record = add_numeric_display_contract(record)
            self._add_display_values(record)
            records.append(record)
        return evidence, records

    @staticmethod
    def _select_facts(facts: list[SemanticFact]) -> list[SemanticFact]:
        overview = [fact for fact in facts if fact.fact_type == "PLAN_OVERVIEW"]
        demand = [fact for fact in facts if fact.fact_type == "DEMAND_HORIZON_SUMMARY"]
        alignment = [fact for fact in facts if fact.fact_type == "DEMAND_ORDER_ALIGNMENT"]
        baseline = [fact for fact in facts if fact.fact_type == "NO_PLANNED_PURCHASE_BASELINE" and fact.scope is SemanticFactScope.INGREDIENT and float(fact.values.get("shortage_quantity") or 0) > 0]
        operational_risk = [
            fact for fact in facts if fact.fact_type == "INGREDIENT_OPERATIONAL_RISK"
        ]
        risk = [fact for fact in facts if fact.classification is SemanticFactClassification.RISK_SIGNAL and fact.fact_type != "INGREDIENT_OPERATIONAL_RISK"]
        limitations = [fact for fact in facts if fact.classification is SemanticFactClassification.LIMITATION]
        selected_risk = [fact for fact in facts if fact.fact_type == "SELECTED_PLAN_RISK_METRICS"]
        # Preserve every selected-plan operational candidate: communication-plan
        # ranking must see the full set before it chooses one main risk.
        return [*overview, *selected_risk, *demand, *alignment, *baseline, *operational_risk, *risk, *limitations]

    @staticmethod
    def _add_display_values(record: dict[str, Any]) -> None:
        # Compatibility aliases only; display.py remains the formatter.
        if not isinstance(record.get("display_values"), dict):
            add_numeric_display_contract(record)
        display = record.get("display_values", {})
        if "total_purchase_cost" in display:
            record["total_purchase_cost_display"] = display["total_purchase_cost"]
        if "expected_fill_rate" in display:
            record["expected_fill_rate_display"] = display["expected_fill_rate"]
        if "first_stockout_date" in display:
            record["first_stockout_date_display"] = display["first_stockout_date"]
