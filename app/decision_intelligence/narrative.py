"""Read-only, grounded natural-language narration for persisted decision facts."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any

from shelfcash_forecast.decision_intelligence.contracts import DecisionGraph
from shelfcash_forecast.decision_intelligence.retrieval import StructuredLocalRetriever, build_retrieval_context

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import Citation, DecisionBriefFacts, DecisionExplanationResponse, ExplanationClaim

logger = logging.getLogger("shelfcash.decision_narrative")

SYSTEM_PROMPT = """Bạn là ShelfCash Decision Narrative Assistant. Diễn giải bằng chứng ShelfCash tính toán thành tiếng Việt ngắn gọn cho quản lý cửa hàng.
QUY TẮC TUYỆT ĐỐI: Chỉ dùng EVIDENCE được cung cấp. Không dự báo, tính lại, tối ưu, thay đổi kế hoạch, suy đoán dữ liệu thiếu, hay tạo facts mới. Mọi factual claim phải có evidence_ids hợp lệ. Nếu chưa đủ bằng chứng xác nhận nguyên nhân, nói rõ: \"Chưa đủ dữ liệu để xác nhận nguyên nhân này.\" Không nhắc đến model, prompt, retrieval, evidence hay implementation. Không hiển thị UUID khi đã có tên. Không viết chain-of-thought.
Trả về DUY NHẤT JSON hợp lệ: {\"answer\": string, \"claims\": [{\"type\": string, \"text\": string, \"evidence_ids\": [string]}], \"used_evidence_ids\": [string]}. Claims phải ngắn gọn; type phải trùng type một evidence mà claim trích dẫn."""

REASON_TEXT = {
    "DEMAND_EXCEEDS_AVAILABLE_SUPPLY": "Nhu cầu dự kiến cao hơn lượng cung sẵn có",
    "LEAD_TIME_PRESSURE": "Thời gian giao hàng tạo áp lực phải đặt sớm",
    "EXPIRING_INVENTORY": "Một phần tồn kho sẽ hết hạn trong kỳ",
    "SAFETY_STOCK_PROTECTION": "Kế hoạch cần duy trì mức tồn an toàn",
    "BUDGET_CONSTRAINT": "Ngân sách ảnh hưởng đến phương án mua",
    "PACK_SIZE_ROUNDING": "Số lượng được làm tròn theo quy cách đóng gói",
    "MOQ_CONSTRAINT": "Nhà cung cấp yêu cầu số lượng đặt tối thiểu",
    "SUPPLIER_AVAILABILITY": "Lịch cung ứng ảnh hưởng thời điểm nhận hàng",
    "STOCKOUT_RISK": "Có rủi ro thiếu hàng",
}


def _date(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def aggregate_evidence(brief: DecisionBriefFacts, retrieved_items) -> list[dict[str, Any]]:
    """Perform all daily-demand arithmetic deterministically before Qwen sees it."""
    selected_ids = {item.evidence_id for item in retrieved_items}
    selected_ingredients = {item.entities.get("ingredient_id") for item in retrieved_items if item.entities.get("ingredient_id")}
    records: list[dict[str, Any]] = []
    for item in retrieved_items:
        if item.evidence_type == "first_stage_order":
            records.append({"evidence_id": item.evidence_id, "type": "PROCUREMENT_QUANTITY", "ingredient_id": item.entities.get("ingredient_id"), "supplier_id": item.entities.get("supplier_id"), "value": item.payload.get("quantity"), "unit": item.payload.get("unit"), "purchase_cost": item.payload.get("purchase_cost")})

    grouped: dict[str, list] = defaultdict(list)
    for demand in brief.ingredient_demand:
        if not selected_ingredients or demand.ingredient_id in selected_ingredients:
            grouped[demand.ingredient_id].append(demand)
    for ingredient_id, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: _date(row.target_date))
        unit = ordered[0].unit
        daily = [
            {"target_date": _date(row.target_date), "p25": row.p25, "p50": row.p50, "p75": row.p75}
            for row in ordered
        ]
        peak = max(ordered, key=lambda row: (row.p50 if row.p50 is not None else float("-inf"), _date(row.target_date)))
        matching = [item for item in retrieved_items if item.evidence_type == "ingredient_demand" and item.entities.get("ingredient_id") == ingredient_id]
        ids = [item.evidence_id for item in matching]
        if not ids:
            continue
        record = {"evidence_id": "aggregate:" + ":".join(ids), "type": "DEMAND_HORIZON_SUMMARY", "ingredient_id": ingredient_id, "ingredient_name": ordered[0].ingredient_name, "unit": unit, "period_start": _date(ordered[0].target_date), "period_end": _date(ordered[-1].target_date), "p25_total": sum(row.p25 or 0 for row in ordered), "p50_total": sum(row.p50 or 0 for row in ordered), "p75_total": sum(row.p75 or 0 for row in ordered), "daily_p50_min": min(row.p50 for row in ordered if row.p50 is not None), "daily_p50_max": max(row.p50 for row in ordered if row.p50 is not None), "peak_date": _date(peak.target_date), "peak_p50": peak.p50, "evidence_ids": ids}
        records.append(record)
        # Keep daily facts available for date-specific retrieval; never aggregate them away.
        records.extend({"evidence_id": item.evidence_id, "type": "DEMAND_DAILY", "ingredient_id": ingredient_id, "ingredient_name": ordered[0].ingredient_name, "target_date": item.payload["target_date"], "p25": item.payload.get("p25"), "p50": item.payload.get("p50"), "p75": item.payload.get("p75"), "unit": item.payload.get("unit")} for item in matching)
    for item in retrieved_items:
        if item.evidence_type == "first_stage_order":
            for code in item.payload.get("reason_codes", []):
                if code in REASON_TEXT:
                    records.append({"evidence_id": item.evidence_id, "type": "PROCUREMENT_REASON", "ingredient_id": item.entities.get("ingredient_id"), "code": code, "meaning": REASON_TEXT[code]})
    return records


class DecisionNarrativeProvider:
    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings
        self.deterministic = ShelfCashDecisionIntelligenceAdapter()

    def explain(self, brief: DecisionBriefFacts, *, question: str | None, language: str, detail_level: str) -> DecisionExplanationResponse:
        fallback = self.deterministic.explain(brief, question=question, language=language, detail_level=detail_level)
        if self.settings.decision_narrative_provider != "local_qwen" or not self.llm_provider.available:
            return fallback
        return self._qwen_or_fallback(brief, question, language, detail_level, fallback)

    def _qwen_or_fallback(self, brief, question, language, detail_level, fallback):
        started = time.monotonic()
        try:
            logger.info("decision_narrative_started decision_run_id=%s provider=local_qwen", brief.decision_run_id)
            evidence = self.deterministic._evidence(brief)
            resolved_question = question or ("Why is this plan recommended?" if language == "en" else "Tại sao kế hoạch này được đề xuất?")
            retrieved = StructuredLocalRetriever().retrieve(resolved_question, evidence, DecisionGraph(request_id=brief.decision_run_id, nodes=[], edges=[]), context=build_retrieval_context(resolved_question, evidence, recommended_strategy=(brief.recommendation.strategy or "").upper() or None))
            structured = aggregate_evidence(brief, retrieved.items)
            if not structured:
                raise ValueError("no_retrieved_evidence")
            logger.info("decision_narrative_retrieval_completed decision_run_id=%s evidence_count=%d intent=%s", brief.decision_run_id, len(structured), retrieved.intent)
            payload = {"question": resolved_question, "language": language, "detail_level": detail_level, "evidence": structured}
            raw = asyncio.run(self.llm_provider.generate_json(SYSTEM_PROMPT, payload, max_new_tokens=self.settings.decision_narrative_max_new_tokens))
            logger.info("decision_narrative_qwen_completed decision_run_id=%s provider=local_qwen", brief.decision_run_id)
            response = self._guard(raw, structured, evidence.items, brief, language, detail_level, retrieved.intent)
            logger.info("decision_narrative_grounding_passed decision_run_id=%s provider=local_qwen duration_ms=%d", brief.decision_run_id, int((time.monotonic() - started) * 1000))
            return response
        except Exception as exc:
            logger.warning("decision_narrative_grounding_failed decision_run_id=%s provider=local_qwen reason=%s", brief.decision_run_id, type(exc).__name__)
            logger.warning("decision_narrative_fallback decision_run_id=%s provider=local_qwen reason=%s duration_ms=%d", brief.decision_run_id, type(exc).__name__, int((time.monotonic() - started) * 1000))
            return fallback.model_copy(update={"provider": "deterministic_fallback"})

    def _guard(self, raw, structured, evidence_items, brief, language, detail_level, intent):
        if not isinstance(raw.get("answer"), str) or not isinstance(raw.get("claims"), list):
            raise ValueError("malformed_qwen_output")
        by_id = {item.evidence_id: item for item in evidence_items}
        structured_by_id = {item["evidence_id"]: item for item in structured}
        allowed_ids = set(structured_by_id)
        claims = []
        citation_ids = set()
        for claim in raw["claims"]:
            if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
                raise ValueError("malformed_claim")
            ids = claim.get("evidence_ids")
            if not isinstance(ids, list) or not ids or not set(ids) <= allowed_ids:
                raise ValueError("unsupported_evidence_id")
            supported_types = {structured_by_id[evidence_id]["type"] for evidence_id in ids}
            if claim.get("type") not in supported_types:
                raise ValueError("unsupported_claim_type")
            self._validate_numbers(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_entities(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids], brief)
            self._validate_supported_concepts(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            claim_source_ids = sorted({source_id for evidence_id in ids for source_id in structured_by_id[evidence_id].get("evidence_ids", [evidence_id])})
            claims.append(ExplanationClaim(type=claim["type"], value=claim["text"], evidence_ids=claim_source_ids))
            for evidence_id in ids:
                citation_ids.update(structured_by_id[evidence_id].get("evidence_ids", [evidence_id]))
        used = raw.get("used_evidence_ids", list(citation_ids))
        if not isinstance(used, list) or not set(used) <= allowed_ids:
            raise ValueError("unsupported_used_evidence_id")
        citations = [Citation(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object) for item in evidence_items if item.evidence_id in citation_ids]
        entities = {"ingredient_ids": sorted({item.entities["ingredient_id"] for item in evidence_items if item.evidence_id in citation_ids and item.entities.get("ingredient_id")}), "supplier_ids": sorted({item.entities["supplier_id"] for item in evidence_items if item.evidence_id in citation_ids and item.entities.get("supplier_id")})}
        return DecisionExplanationResponse(source="local_qwen", language=language, detail_level=detail_level, summary=raw["answer"], why_this_plan=[raw["answer"]], main_risks=brief.critic.warnings, tradeoffs=[], important_assumptions=["Narrative is grounded only in the persisted decision package."], decision_run_id=brief.decision_run_id, answer=raw["answer"], intent=str(intent).upper(), entities=entities, claims=claims, citations=citations, grounded=True, provider="local_qwen")

    @staticmethod
    def _validate_numbers(text: str, payloads: list[dict]):
        numbers = re.findall(r"(?<![\w-])\d+(?:[.,]\d+)?", text)
        supported = {round(float(value), 9) for payload in payloads for value in payload.values() if isinstance(value, (int, float))}
        for number in numbers:
            if round(float(number.replace(",", ".")), 9) not in supported:
                raise ValueError("unsupported_numeric_claim")

    @staticmethod
    def _validate_entities(text: str, items, brief):
        name_to_id = {row.ingredient_name.lower(): row.ingredient_id for row in brief.ingredient_demand if row.ingredient_name}
        name_to_id.update({row.ingredient_name.lower(): row.ingredient_id for row in brief.procurement_rows if row.ingredient_name})
        cited_ids = {item.get("ingredient_id") for item in items}
        for name, ingredient_id in name_to_id.items():
            if name in text.lower() and ingredient_id not in cited_ids:
                raise ValueError("entity_mismatch")

    @staticmethod
    def _validate_supported_concepts(text: str, items: list[dict]):
        lowered = text.lower()
        required = {
            ("tồn an toàn", "safety stock"): lambda: any(item["type"] == "SAFETY_STOCK" for item in items),
            ("moq", "đặt tối thiểu"): lambda: any(item["type"] == "MOQ" or item.get("code") == "MOQ_CONSTRAINT" for item in items),
            ("hạn dùng", "hết hạn"): lambda: any(item["type"] == "EXPIRY" or item.get("code") == "EXPIRING_INVENTORY" for item in items),
            ("lead time", "thời gian giao"): lambda: any(item["type"] == "LEAD_TIME" or item.get("code") == "LEAD_TIME_PRESSURE" for item in items),
            ("ngân sách", "budget"): lambda: any(item["type"] == "BUDGET" for item in items),
            ("rủi ro", "xác suất thiếu"): lambda: any(item["type"] == "RISK" for item in items),
        }
        for phrases, is_supported in required.items():
            if any(phrase in lowered for phrase in phrases) and not is_supported():
                raise ValueError("unsupported_causal_concept")
