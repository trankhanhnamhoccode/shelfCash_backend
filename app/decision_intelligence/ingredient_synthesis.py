"""Backend-owned ingredient presentation with isolated per-item narration."""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import LLMUnavailableError
from app.core.logging_context import get_request_id
from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    DecisionBriefFacts, IngredientSynthesis, IngredientSynthesisLLMResponse,
)
from app.decision_intelligence.display import add_numeric_display_contract
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import SemanticFact
from app.llm.tasks import LLMFailureStage, LLMTask
from app.llm.runtime import generate_json_sync

try:
    import httpx
except ImportError:  # pragma: no cover - OpenRouter installs httpx in production.
    httpx = None

logger = logging.getLogger("shelfcash.ingredient_synthesis")

SYSTEM_PROMPT = """Bạn là lớp diễn đạt nguyên liệu của ShelfCash dành cho quản lý cửa hàng.
Chỉ diễn đạt EVIDENCE đã cung cấp, đúng INGREDIENT_ID tương ứng. Không tính toán,
không phân loại mức độ quan trọng, không đổi kế hoạch, không suy ra nguyên nhân.
Được phép đặt cạnh hai sự kiện theo thời gian khi cả hai đều có bằng chứng, nhưng
không được dùng từ chỉ quan hệ nguyên nhân như 'vì', 'do' nếu causal_allowed=false.
Trả về JSON đúng schema: mỗi item phải có headline, summary, claims và used_evidence_ids.
Mọi con số, ngày và đơn vị phải lặp lại nguyên văn display_values trong evidence."""


class IngredientSynthesisProvider:
    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings
        self.adapter = ShelfCashDecisionIntelligenceAdapter()
        self.guard = DecisionNarrativeProvider(None, settings)
        # This is deliberately request-scoped state on a short-lived provider.
        # It is persisted by the decision planning service as internal package
        # metadata, never returned in the manager-facing Decision Brief.
        self.last_diagnostics: dict[str, Any] | None = None

    def synthesize(self, brief: DecisionBriefFacts, facts: list[SemanticFact]) -> list[IngredientSynthesis]:
        records, evidence_items = self._records(brief, facts)
        by_ingredient: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if record.get("ingredient_id"):
                by_ingredient[str(record["ingredient_id"])].append(record)
        # The semantic package is authoritative for ingredient-level selected
        # risk rows too, including a defensive case where an old package has a
        # risk metric but omitted its demand/order presentation row.
        ingredient_ids = sorted(
            {row.ingredient_id for row in brief.ingredient_demand}
            | {row.ingredient_id for row in brief.procurement_rows}
            | set(by_ingredient)
        )
        risk_by_ingredient: dict[str, list] = defaultdict(list)
        for detail in brief.risk_details:
            if detail.ingredient_id:
                risk_by_ingredient[detail.ingredient_id].append(detail)

        self.last_diagnostics = self._new_diagnostics(brief, ingredient_ids)

        prepared: dict[str, tuple[IngredientSynthesis, list[dict[str, Any]], str]] = {}
        eligible: list[dict[str, Any]] = []
        for ingredient_id in ingredient_ids:
            item_records = by_ingredient[ingredient_id]
            importance = self._importance(item_records, risk_by_ingredient[ingredient_id])
            fallback = self._rule(brief, ingredient_id, item_records, importance)
            prepared[ingredient_id] = (fallback, item_records, importance)
            if importance == "critical":
                eligible.append({
                    "ingredient_id": ingredient_id,
                    "communication_plan": self._communication_plan(item_records),
                    "evidence": item_records,
                })
            logger.info("ingredient_synthesis_route decision_run_id=%s ingredient_id=%s importance=%s llm_eligible=%s evidence_ids=%s", brief.decision_run_id, ingredient_id, importance, importance == "critical", fallback.evidence_ids)

        diagnostics = self.last_diagnostics
        assert diagnostics is not None
        diagnostics["eligible_count"] = len(eligible)
        diagnostics["eligible_ingredient_ids"] = [entry["ingredient_id"] for entry in eligible]
        diagnostics["normal_count"] = sum(1 for value in prepared.values() if value[2] == "normal")
        diagnostics["watch_count"] = sum(1 for value in prepared.values() if value[2] == "watch")
        diagnostics["critical_count"] = sum(1 for value in prepared.values() if value[2] == "critical")
        if not eligible:
            diagnostics.update(status="not_attempted", failure_stage="NOT_ATTEMPTED")
            return [prepared[item][0] for item in ingredient_ids]
        if not self.llm_provider or not self.llm_provider.available:
            diagnostics.update(status="not_attempted", failure_stage="PROVIDER_UNAVAILABLE")
            diagnostics["items"] = [
                self._item_diagnostic(prepared[entry["ingredient_id"]][0], "fallback", "PROVIDER_UNAVAILABLE", "provider_unavailable")
                for entry in eligible
            ]
            return [prepared[item][0] for item in ingredient_ids]
        return self._per_item_or_fallback(brief, prepared, eligible, evidence_items, ingredient_ids)

    def _new_diagnostics(self, brief: DecisionBriefFacts, ingredient_ids: list[str]) -> dict[str, Any]:
        configured_model = "qwen/qwen3.5-9b"
        if self.llm_provider and hasattr(self.llm_provider, "task_profile"):
            try:
                configured_model = self.llm_provider.task_profile(LLMTask.INGREDIENT_SYNTHESIS).model
            except Exception:
                # Diagnostics must never change the existing fallback path.
                pass
        return {
            "decision_run_id": brief.decision_run_id,
            "correlation_id": get_request_id(),
            "task": LLMTask.INGREDIENT_SYNTHESIS.value,
            "llm_attempted": False,
            "total_ingredient_count": len(ingredient_ids),
            "eligible_count": 0,
            "eligible_ingredient_ids": [],
            "provider_call_count": 0,
            "attempt_count": 0,
            "configured_model": configured_model,
            "resolved_model": None,
            "provider": None,
            "status": "not_attempted",
            "failure_stage": "NOT_ATTEMPTED",
            "raw_response_present": False,
            "content_present": False,
            "raw_response": None,
            "items": [],
        }

    def _records(self, brief, facts):
        evidence = self.adapter._evidence(brief, semantic_facts=facts)
        item_by_fact = {
            str(item.payload.get("semantic_fact_id")): item for item in evidence.items
            if item.evidence_type.startswith("semantic_")
        }
        records = []
        for fact in facts:
            if fact.entities.get("ingredient_id") is None:
                continue
            item = item_by_fact.get(fact.fact_id)
            if item is None:
                continue
            record = {"evidence_id": item.evidence_id, "evidence_ids": [item.evidence_id], "type": fact.fact_type, **fact.entities, **fact.values}
            records.append(add_numeric_display_contract(record))
        return records, evidence.items

    @staticmethod
    def _importance(records, risk_details) -> str:
        operational = [item for item in records if item.get("type") == "INGREDIENT_OPERATIONAL_RISK"]
        # A stockout date is an operational signal. Existing RiskDetail
        # severity, derived from risk metadata, is the authoritative routing
        # source for an LLM-critical item.
        if any(item.severity == "critical" for item in risk_details):
            return "critical"
        if operational or any(item.severity == "warning" for item in risk_details):
            return "watch"
        return "normal"

    def _rule(self, brief, ingredient_id, records, importance) -> IngredientSynthesis:
        by_type = {str(item["type"]): item for item in records}
        demand = by_type.get("DEMAND_HORIZON_SUMMARY")
        risk = by_type.get("INGREDIENT_OPERATIONAL_RISK")
        name = next((str(item.get("ingredient_name")) for item in records if item.get("ingredient_name")), ingredient_id)
        unit = next((str(item.get("unit")) for item in records if item.get("unit")), None)
        ids = [str(item["evidence_id"]) for item in records if item["type"] in {"INGREDIENT_OPERATIONAL_RISK", "DEMAND_HORIZON_SUMMARY", "PROCUREMENT_QUANTITY", "DEMAND_ORDER_ALIGNMENT"}]
        if importance == "normal":
            headline = "Kế hoạch chưa ghi nhận rủi ro thiếu hàng đáng kể"
            if demand and demand.get("display_values", {}).get("p50_total"):
                summary = f"Nhu cầu trong {brief.forecast.horizon_days} ngày tới khoảng {demand['display_values']['p50_total']} {unit or ''}. Kế hoạch hiện tại chưa ghi nhận rủi ro thiếu hàng đáng kể đối với nguyên liệu này."
            else:
                summary = "Kế hoạch hiện tại chưa ghi nhận rủi ro thiếu hàng đáng kể đối với nguyên liệu này."
        elif importance == "watch":
            headline = "Có tín hiệu cần theo dõi trong kỳ kế hoạch"
            if risk and risk.get("first_stockout_date"):
                summary = f"Kế hoạch có tín hiệu thiếu hàng từ {risk['display_values'].get('first_stockout_date', risk['first_stockout_date'])}. Cần theo dõi nguyên liệu này trong kỳ kế hoạch."
            else:
                summary = "Kế hoạch hiện tại đáp ứng phần lớn nhu cầu, tuy nhiên nguyên liệu này có tín hiệu cần theo dõi trong kỳ kế hoạch."
        else:
            headline = "Cần ưu tiên theo dõi rủi ro thiếu hàng"
            if risk and risk.get("first_stockout_date"):
                summary = f"{name} có nguy cơ thiếu từ {risk['display_values'].get('first_stockout_date', risk['first_stockout_date'])} trong kỳ kế hoạch."
            else:
                summary = f"{name} có rủi ro vận hành cần được ưu tiên theo dõi trong kỳ kế hoạch."
        return IngredientSynthesis(ingredient_id=ingredient_id, ingredient_name=name, unit=unit, importance=importance, source="rule_based", headline=headline, summary=summary, evidence_ids=list(dict.fromkeys(ids)))

    @staticmethod
    def _communication_plan(records):
        primary = [item["evidence_id"] for item in records if item["type"] == "INGREDIENT_OPERATIONAL_RISK"][:1]
        supporting = [item["evidence_id"] for item in records if item["type"] in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY", "DEMAND_ORDER_ALIGNMENT"}][:3]
        return {"primary": primary, "supporting": supporting, "causal_allowed": False}

    def _per_item_or_fallback(self, brief, prepared, eligible, evidence_items, ingredient_ids):
        diagnostics = self.last_diagnostics
        assert diagnostics is not None
        diagnostics.update(llm_attempted=True, status="failed", failure_stage=None)
        results = {ingredient_id: prepared[ingredient_id][0] for ingredient_id in ingredient_ids}
        for entry in eligible:
            ingredient_id = entry["ingredient_id"]
            fallback, records, importance = prepared[ingredient_id]
            context = {"decision_run_id": brief.decision_run_id, "correlation_id": diagnostics["correlation_id"], "ingredient_id": ingredient_id}
            item_diagnostic = self._item_diagnostic(fallback, "fallback", None, None)
            item_diagnostic.update(llm_attempted=True, provider_call_count=1, raw_response_present=False, content_present=False, attempt_count=0)
            diagnostics["provider_call_count"] += 1
            try:
                raw = self._run({"task": "ingredient_synthesis", "language": "vi", "ingredient_id": ingredient_id, "communication_plan": entry["communication_plan"], "evidence": entry["evidence"], "style_examples": []}, context)
                self._apply_item_gateway_diagnostics(item_diagnostic, context)
                item = IngredientSynthesisLLMResponse.model_validate(raw)
                allowed = {record["evidence_id"] for record in records}
                if not set(item.used_evidence_ids) <= allowed:
                    raise ValueError("ingredient_evidence_entity_mismatch")
                guarded = self.guard._guard({"answer": f"{item.headline}. {item.summary}", "claims": [claim.model_dump(mode="json") for claim in item.claims], "used_evidence_ids": item.used_evidence_ids}, records, evidence_items, brief, "vi", "simple", "INGREDIENT_SYNTHESIS", target_ingredient_id=ingredient_id)
                results[ingredient_id] = IngredientSynthesis(ingredient_id=ingredient_id, ingredient_name=fallback.ingredient_name, unit=fallback.unit, importance=importance, source="llm", headline=item.headline, summary=item.summary, evidence_ids=[citation.evidence_id for citation in guarded.citations])
                item_diagnostic.update(status="llm_success", failure_stage=None, used_evidence_ids=list(item.used_evidence_ids))
            except Exception as exc:
                stage = self._batch_failure_stage(exc, context)
                # Gateway stages describe transport and response failures.  A
                # guard error is item-local and must retain its more specific
                # grounding category instead of being collapsed to a local
                # ValueError/INTERNAL_RUNTIME classification.
                item_stage, item_reason = self._item_failure(exc)
                if stage != "SCHEMA_VALIDATION" and item_stage != "BUSINESS_VALIDATION":
                    stage, reason = item_stage, item_reason
                else:
                    reason = f"item_{stage.lower()}" if stage != "UNKNOWN" else item_reason
                self._apply_exception_diagnostics(item_diagnostic, exc)
                self._apply_item_gateway_diagnostics(item_diagnostic, context)
                item_diagnostic.update(status="fallback", failure_stage=stage, fallback_reason=reason)
                results[ingredient_id] = self._fallback(fallback)
            diagnostics["items"].append(item_diagnostic)
        succeeded = sum(1 for item in diagnostics["items"] if item["status"] == "llm_success")
        diagnostics.update(llm_success_count=succeeded, fallback_count=len(eligible) - succeeded, status="success" if succeeded == len(eligible) else "partial_success" if succeeded else "failed", failure_stage=None)
        return [results[ingredient_id] for ingredient_id in ingredient_ids]

    @staticmethod
    def _item_diagnostic(item, status: str, failure_stage: str | None, fallback_reason: str | None, used_evidence_ids: list[str] | None = None) -> dict[str, Any]:
        return {
            "ingredient_id": item.ingredient_id,
            "ingredient_name": item.ingredient_name,
            "status": status,
            "failure_stage": failure_stage,
            "fallback_reason": fallback_reason,
            "used_evidence_ids": list(used_evidence_ids or []),
        }

    @staticmethod
    def _batch_failure_stage(exc: Exception, context: dict[str, Any]) -> str:
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details.get("failure_stage"):
            stage = str(details["failure_stage"])
            http_status = getattr(exc, "http_status", None)
            gateway = context.get("openrouter_diagnostics")
            if isinstance(gateway, dict):
                http_status = gateway.get("http_status") or http_status
            if stage == "HTTP" and http_status == 429:
                return "RATE_LIMIT"
            if stage == "HTTP" and isinstance(http_status, int) and http_status >= 500:
                return "PROVIDER_UNAVAILABLE"
            return stage
        gateway = context.get("openrouter_diagnostics")
        if isinstance(gateway, dict) and gateway.get("failure_category"):
            return str(gateway["failure_category"])
        if isinstance(exc, PydanticValidationError):
            return "SCHEMA_VALIDATION"
        if isinstance(exc, LLMUnavailableError):
            return "PROVIDER_UNAVAILABLE"
        if isinstance(exc, asyncio.TimeoutError) or (httpx is not None and isinstance(exc, httpx.TimeoutException)):
            return LLMFailureStage.TIMEOUT.value
        if httpx is not None and isinstance(exc, httpx.RequestError):
            return LLMFailureStage.NETWORK.value
        # This is a defensive boundary only. OpenRouterLLMGateway normalizes
        # its own failures; an exception reaching here unclassified originated
        # in local orchestration, a custom provider, or the sync/async bridge.
        if isinstance(exc, (RuntimeError, TypeError, ValueError, OSError)):
            return LLMFailureStage.INTERNAL_RUNTIME.value
        return "UNKNOWN"

    @staticmethod
    def _item_failure(exc: Exception) -> tuple[str, str]:
        message = str(exc).lower()
        if "numeric" in message or "range_semantic" in message:
            return "NUMERIC_GROUNDING", "unsupported_numeric_claim"
        if "causal" in message:
            return "CAUSAL_GROUNDING", "unsupported_causal_claim"
        if "entity" in message or "ingredient_evidence" in message:
            return "ENTITY_GROUNDING", "ingredient_evidence_entity_mismatch"
        if "claim" in message or "evidence" in message or "ground" in message:
            return "GROUNDING", "grounding_validation_failed"
        return "BUSINESS_VALIDATION", type(exc).__name__

    @staticmethod
    def _safe_raw_response(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        # Gateways only place response bodies/content here, never request
        # headers. Redact defensively before persisting developer metadata.
        redacted = re.sub(
            r'''(?i)(["']?(?:authorization|api[_-]?key)["']?\s*[:=]\s*["']?)(?:bearer\s+)?[^,\s"'}]+''',
            r"\1[REDACTED]",
            value,
        )
        return redacted[:20000]

    @staticmethod
    def _safe_exception_message(exc: Exception) -> str:
        value = str(exc)
        redacted = re.sub(
            r'''(?i)(authorization|api[_-]?key)\s*[:=]\s*(?:bearer\s+)?[^,\s"'}]+''',
            r"\1=[REDACTED]",
            value,
        )
        return redacted[:500]

    def _apply_exception_diagnostics(self, diagnostics: dict[str, Any], exc: Exception) -> None:
        details = getattr(exc, "details", None)
        details = details if isinstance(details, dict) else {}
        diagnostics["exception_type"] = str(details.get("exception_type") or type(exc).__name__)
        diagnostics["exception_message"] = str(details.get("exception_message") or self._safe_exception_message(exc))[:500]
        if details.get("origin") is not None:
            diagnostics["exception_origin"] = str(details["origin"])

    def _apply_gateway_diagnostics(self, diagnostics: dict[str, Any], context: dict[str, Any]) -> None:
        gateway = context.get("openrouter_diagnostics")
        if isinstance(gateway, dict):
            diagnostics["attempt_count"] = int(gateway.get("attempt_count") or diagnostics["attempt_count"])
            diagnostics["correlation_id"] = gateway.get("correlation_id") or diagnostics["correlation_id"]
            diagnostics["resolved_model"] = gateway.get("resolved_model") or diagnostics["resolved_model"]
            diagnostics["provider"] = gateway.get("resolved_provider") or diagnostics["provider"]
            diagnostics["raw_response_present"] = bool(gateway.get("raw_response_present", diagnostics["raw_response_present"]))
            diagnostics["content_present"] = bool(gateway.get("content_present", diagnostics["content_present"]))
            if gateway.get("http_status") is not None:
                diagnostics["http_status"] = gateway["http_status"]
            for key in ("generation_id", "exception_type", "exception_message", "exception_origin", "validation_stage"):
                if gateway.get(key) is not None:
                    diagnostics[key] = gateway[key]
        metadata = context.get("openrouter_metadata")
        if isinstance(metadata, dict):
            diagnostics["resolved_model"] = metadata.get("resolved_model") or diagnostics["resolved_model"]
            diagnostics["provider"] = metadata.get("resolved_provider") or diagnostics["provider"]
            if metadata.get("finish_reason") is not None:
                diagnostics["finish_reason"] = metadata["finish_reason"]
            if metadata.get("generation_id") is not None:
                diagnostics["generation_id"] = metadata["generation_id"]
        raw = context.get("openrouter_raw_content") or context.get("openrouter_raw_response")
        safe_raw = self._safe_raw_response(raw)
        if safe_raw is not None:
            diagnostics["raw_response"] = safe_raw
            diagnostics["raw_response_present"] = True

    def _apply_item_gateway_diagnostics(self, diagnostics: dict[str, Any], context: dict[str, Any]) -> None:
        gateway = context.get("openrouter_diagnostics")
        if isinstance(gateway, dict):
            diagnostics["attempt_count"] = int(gateway.get("attempt_count") or 0)
            diagnostics["provider"] = gateway.get("resolved_provider")
            diagnostics["resolved_model"] = gateway.get("resolved_model")
            diagnostics["raw_response_present"] = bool(gateway.get("raw_response_present"))
            diagnostics["content_present"] = bool(gateway.get("content_present"))
            if gateway.get("http_status") is not None:
                diagnostics["http_status"] = gateway["http_status"]
        metadata = context.get("openrouter_metadata")
        if isinstance(metadata, dict):
            diagnostics["provider"] = metadata.get("resolved_provider") or diagnostics.get("provider")
            diagnostics["resolved_model"] = metadata.get("resolved_model") or diagnostics.get("resolved_model")
        raw = self._safe_raw_response(context.get("openrouter_raw_content") or context.get("openrouter_raw_response"))
        if raw is not None:
            diagnostics["raw_response"] = raw
            diagnostics["raw_response_present"] = True

    @staticmethod
    def _fallback(item):
        return item.model_copy(update={"source": "deterministic_fallback"})

    def _run(self, payload, context):
        return generate_json_sync(
            self.llm_provider, SYSTEM_PROMPT, payload,
            task=LLMTask.INGREDIENT_SYNTHESIS, request_context=context,
        )
