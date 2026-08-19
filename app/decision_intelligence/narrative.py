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

SYSTEM_PROMPT = """Bạn là ShelfCash Decision Narrative Assistant.

Nhiệm vụ của bạn là diễn giải thông tin ShelfCash đã tính toán thành tiếng Việt ngắn gọn, tự nhiên và dễ hiểu cho quản lý cửa hàng.

QUY TẮC TUYỆT ĐỐI:

1. Chỉ sử dụng thông tin có trong EVIDENCE được cung cấp.

2. Không tự:
- dự báo nhu cầu;
- cộng, trừ, tính tổng hoặc tính lại số liệu;
- tối ưu hoặc thay đổi kế hoạch;
- chọn số lượng mua;
- chọn nhà cung cấp;
- suy đoán dữ liệu còn thiếu;
- tạo thêm facts, nguyên nhân hoặc kết luận mới.

3. Mọi thông tin thực tế xuất hiện trong "answer" phải được hỗ trợ bởi ít nhất một claim trong "claims".

4. Mỗi claim phải:
- ngắn gọn;
- chỉ chứa thông tin được hỗ trợ bởi evidence mà claim trích dẫn;
- có ít nhất một evidence_id hợp lệ;
- chỉ dùng evidence_id thực sự tồn tại trong EVIDENCE;
- có "type" trùng với type của ít nhất một evidence được claim trích dẫn.

5. Không tự tạo evidence_id.

6. Mọi con số, ngày tháng, số lượng, đơn vị, tên nguyên liệu hoặc tên nhà cung cấp được nhắc đến phải xuất hiện trong evidence được claim trích dẫn.

7. Không tự tính toán từ nhiều bản ghi.

Ví dụ:
Nếu EVIDENCE chứa nhu cầu từng ngày nhưng không chứa DEMAND_HORIZON_SUMMARY,
không tự cộng các ngày để tạo tổng nhu cầu.

Nếu EVIDENCE đã chứa DEMAND_HORIZON_SUMMARY,
được phép diễn giải các giá trị tổng, min, max và peak đã có sẵn trong summary đó.

8. Chỉ sử dụng ngôn ngữ thể hiện nguyên nhân như:
"vì", "do", "nên", "do đó", "dẫn đến", "để tránh", "khiến"
khi EVIDENCE có PROCUREMENT_REASON hoặc một evidence trực tiếp xác nhận nguyên nhân đó.

Không suy luận quan hệ nguyên nhân chỉ vì hai facts cùng xuất hiện.

Ví dụ:
- Có nhu cầu dự kiến và có đơn đặt hàng không tự động chứng minh nhu cầu là nguyên nhân của lượng đặt.
- Có safety stock không tự động chứng minh safety stock quyết định lượng mua.
- Có lead time không tự động chứng minh lead time là nguyên nhân phải đặt sớm.
- Có warning không tự động chứng minh warning là nguyên nhân của kế hoạch.

9. Khi có PROCUREMENT_REASON, hãy diễn giải meaning của reason đó bằng câu tự nhiên và có thể dùng nó để giải thích "tại sao".

10. Nếu người dùng hỏi "tại sao", "vì sao" hoặc hỏi nguyên nhân nhưng EVIDENCE không có bằng chứng xác nhận nguyên nhân, hãy trả lời rõ:
"Chưa đủ dữ liệu để xác nhận nguyên nhân này."

Sau câu đó, có thể nêu ngắn gọn những facts liên quan đã biết nếu chúng có evidence hợp lệ.

11. Nếu có tên nguyên liệu hoặc tên nhà cung cấp thì dùng tên.
Không hiển thị UUID khi đã có tên tương ứng.

12. Không nhắc đến:
- model;
- prompt;
- retrieval;
- evidence;
- evidence pipeline;
- implementation;
- database;
- UUID;
- chain-of-thought.

13. Văn phong:
- tiếng Việt tự nhiên;
- dành cho quản lý cửa hàng;
- câu ngắn, rõ ràng;
- với detail_level="simple", ưu tiên 1 đến 3 câu;
- không lặp lại toàn bộ dữ liệu từng ngày nếu đã có DEMAND_HORIZON_SUMMARY;
- không dùng các cụm kỹ thuật như "Persisted ingredient demand", "first stage order" hoặc tên nội bộ của hệ thống.

14. Với DEMAND_HORIZON_SUMMARY:
- có thể nói tổng nhu cầu dự kiến trong kỳ;
- có thể nói khoảng nhu cầu mỗi ngày;
- có thể nói ngày có nhu cầu cao nhất;
- chỉ sử dụng đúng các giá trị đã có trong evidence.

15. Với DEMAND_DAILY:
chỉ dùng khi câu hỏi liên quan trực tiếp đến một ngày cụ thể hoặc khi không có summary phù hợp.

16. Với PROCUREMENT_QUANTITY:
diễn giải thành câu tự nhiên như:
"Kế hoạch đề xuất nhập 0,5 kg bột matcha."
Không dùng UUID thay cho tên nguyên liệu.

17. Với PROCUREMENT_REASON:
diễn giải "meaning" thành nguyên nhân dễ hiểu cho quản lý cửa hàng.
Không mở rộng thêm nguyên nhân ngoài nội dung reason đã cung cấp.

18. Không nói:
"để đảm bảo không thiếu hàng",
"để đáp ứng toàn bộ nhu cầu",
"để duy trì tồn kho an toàn",
"do tồn kho không đủ",
hoặc các kết luận tương tự nếu EVIDENCE không trực tiếp hỗ trợ chúng.

19. "used_evidence_ids" phải chứa đúng các evidence_id đã thực sự được sử dụng trong claims.
Không thêm ID không dùng và không dùng ID không tồn tại trong EVIDENCE.

20. Không viết chain-of-thought, quá trình suy luận hoặc giải thích cách bạn tạo câu trả lời.

OUTPUT:

Chỉ trả về DUY NHẤT một JSON hợp lệ.
Không markdown.
Không ```json.
Không thêm bất kỳ chữ nào trước hoặc sau JSON.

Schema bắt buộc:

{
  "answer": "string",
  "claims": [
    {
      "type": "string",
      "text": "string",
      "evidence_ids": ["string"]
    }
  ],
  "used_evidence_ids": ["string"]
}

Nếu không đủ dữ liệu để trả lời nguyên nhân:

{
  "answer": "Chưa đủ dữ liệu để xác nhận nguyên nhân này.",
  "claims": [],
  "used_evidence_ids": []
}
"""

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
        if not self.llm_provider or not self.llm_provider.available:
            return fallback
        return self._qwen_or_fallback(brief, question, language, detail_level, fallback)

    def _qwen_or_fallback(self, brief, question, language, detail_level, fallback):
        started = time.monotonic()
        raw = None
        try:
            logger.info("decision_narrative_started decision_run_id=%s provider=openrouter_qwen", brief.decision_run_id)
            evidence = self.deterministic._evidence(brief)
            resolved_question = question or ("Why is this plan recommended?" if language == "en" else "Tại sao kế hoạch này được đề xuất?")
            retrieved = StructuredLocalRetriever().retrieve(resolved_question, evidence, DecisionGraph(request_id=brief.decision_run_id, nodes=[], edges=[]), context=build_retrieval_context(resolved_question, evidence, recommended_strategy=(brief.recommendation.strategy or "").upper() or None))
            structured = aggregate_evidence(brief, retrieved.items)
            if not structured:
                raise ValueError("no_retrieved_evidence")
            logger.info("decision_narrative_retrieval_completed decision_run_id=%s evidence_count=%d intent=%s", brief.decision_run_id, len(structured), retrieved.intent)
            payload = {"question": resolved_question, "language": language, "detail_level": detail_level, "evidence": structured}
            raw = asyncio.run(self.llm_provider.generate_json(SYSTEM_PROMPT, payload, max_new_tokens=getattr(self.settings, "decision_narrative_max_new_tokens", 2000)))
            logger.info("decision_narrative_qwen_completed decision_run_id=%s provider=openrouter_qwen", brief.decision_run_id)
            response = self._guard(raw, structured, evidence.items, brief, language, detail_level, retrieved.intent)
            logger.info("decision_narrative_grounding_passed decision_run_id=%s provider=openrouter_qwen duration_ms=%d", brief.decision_run_id, int((time.monotonic() - started) * 1000))
            return response
        except Exception as exc:
            logger.warning("decision_narrative_grounding_failed decision_run_id=%s provider=openrouter_qwen reason=%s", brief.decision_run_id, type(exc).__name__)
            logger.warning("decision_narrative_fallback decision_run_id=%s provider=openrouter_qwen reason=%s duration_ms=%d", brief.decision_run_id, type(exc).__name__, int((time.monotonic() - started) * 1000))
            update_dict: dict[str, Any] = {"provider": "deterministic_fallback"}
            if raw is not None:
                update_dict["raw_response"] = raw
            return fallback.model_copy(update=update_dict)

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
        return DecisionExplanationResponse(source="openrouter_qwen", language=language, detail_level=detail_level, summary=raw["answer"], why_this_plan=[raw["answer"]], main_risks=brief.critic.warnings, tradeoffs=[], important_assumptions=["Narrative is grounded only in the persisted decision package."], decision_run_id=brief.decision_run_id, answer=raw["answer"], intent=str(intent).upper(), entities=entities, claims=claims, citations=citations, grounded=True, provider="openrouter_qwen", raw_response=raw)

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
