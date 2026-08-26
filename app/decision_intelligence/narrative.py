"""Read-only, grounded natural-language narration for persisted decision facts."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from shelfcash_forecast.decision_intelligence.contracts import DecisionGraph
from shelfcash_forecast.decision_intelligence.retrieval import StructuredLocalRetriever, build_retrieval_context

from app.core.logging_context import get_request_id
from app.decision_intelligence.adapter import (
    ShelfCashDecisionIntelligenceAdapter,
    ingredient_scoped_semantic_facts,
)
from app.decision_intelligence.contracts import Citation, DecisionBriefFacts, DecisionExplanationResponse, DecisionNarrativeLLMResponse, ExplanationClaim
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder, SemanticFact
from app.llm.tasks import LLMFailureStage, LLMTask

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


def aggregate_evidence(
    brief: DecisionBriefFacts,
    retrieved_items,
    semantic_facts: list[SemanticFact] | None = None,
    *,
    include_daily: bool = True,
) -> list[dict[str, Any]]:
    """Adapt canonical deterministic facts to the established Qwen evidence format.

    Horizon arithmetic lives exclusively in ``DecisionSemanticEvidenceBuilder``.
    Legacy package reason codes are intentionally not promoted to trusted causal
    evidence here.
    """
    facts = semantic_facts or DecisionSemanticEvidenceBuilder().build(brief)
    selected_ingredients = {
        item.entities.get("ingredient_id")
        for item in retrieved_items
        if item.entities.get("ingredient_id")
    }
    records: list[dict[str, Any]] = []
    for item in retrieved_items:
        if item.evidence_type == "first_stage_order":
            records.append({
                "evidence_id": item.evidence_id,
                "type": "PROCUREMENT_QUANTITY",
                "ingredient_id": item.entities.get("ingredient_id"),
                "supplier_id": item.entities.get("supplier_id"),
                "value": item.payload.get("quantity"),
                "unit": item.payload.get("unit"),
                "purchase_cost": item.payload.get("purchase_cost"),
                "evidence_ids": [item.evidence_id],
            })

    semantic_items = {
        str(item.payload.get("semantic_fact_id")): item
        for item in retrieved_items
        if item.evidence_type.startswith("semantic_") and item.payload.get("semantic_fact_id")
    }
    daily_by_ingredient: dict[str, list] = defaultdict(list)
    order_by_ingredient: dict[str, list] = defaultdict(list)
    for item in retrieved_items:
        ingredient_id = item.entities.get("ingredient_id")
        if not ingredient_id:
            continue
        if item.evidence_type == "ingredient_demand":
            daily_by_ingredient[ingredient_id].append(item)
        elif item.evidence_type == "first_stage_order":
            order_by_ingredient[ingredient_id].append(item)

    for fact in facts:
        ingredient_id = fact.entities.get("ingredient_id")
        if selected_ingredients and ingredient_id and ingredient_id not in selected_ingredients:
            continue
        if fact.fact_type == "DEMAND_HORIZON_SUMMARY":
            matching = daily_by_ingredient.get(ingredient_id or "", [])
            source_ids = [item.evidence_id for item in matching]
            semantic_item = semantic_items.get(fact.fact_id)
            if not source_ids and semantic_item is not None:
                source_ids = [semantic_item.evidence_id]
            if not source_ids:
                continue
            records.append({
                "evidence_id": semantic_item.evidence_id if semantic_item else "aggregate:" + ":".join(source_ids),
                "type": fact.fact_type,
                "ingredient_id": ingredient_id,
                "classification": fact.classification.value,
                **fact.values,
                "evidence_ids": source_ids,
            })
            if include_daily:
                for item in matching:
                    records.append({
                        "evidence_id": item.evidence_id,
                        "type": "DEMAND_DAILY",
                        "ingredient_id": ingredient_id,
                        "ingredient_name": fact.values.get("ingredient_name"),
                        "target_date": item.payload["target_date"],
                        "p25": item.payload.get("p25"),
                        "p50": item.payload.get("p50"),
                        "p75": item.payload.get("p75"),
                        "unit": item.payload.get("unit"),
                        "evidence_ids": [item.evidence_id],
                    })
        elif fact.fact_type == "DEMAND_ORDER_ALIGNMENT":
            source_ids = [
                item.evidence_id
                for item in daily_by_ingredient.get(ingredient_id or "", [])
                + order_by_ingredient.get(ingredient_id or "", [])
            ]
            semantic_item = semantic_items.get(fact.fact_id)
            if semantic_item is not None:
                source_ids = [semantic_item.evidence_id]
            if not source_ids:
                continue
            records.append({
                "evidence_id": semantic_item.evidence_id if semantic_item else "aggregate:" + ":".join(source_ids),
                "type": fact.fact_type,
                "ingredient_id": ingredient_id,
                "classification": fact.classification.value,
                **fact.values,
                "evidence_ids": source_ids,
            })
        elif fact.fact_type not in {"PROCUREMENT_QUANTITY", "SELECTED_PLAN_RISK_METRICS"}:
            semantic_item = semantic_items.get(fact.fact_id)
            if semantic_item is None:
                continue
            records.append({
                "evidence_id": semantic_item.evidence_id,
                "type": fact.fact_type,
                "classification": fact.classification.value,
                **fact.entities,
                **fact.values,
                "evidence_ids": [semantic_item.evidence_id],
            })

    for item in retrieved_items:
        if item.evidence_type == "inventory_risk":
            records.append({"evidence_id": item.evidence_id, "type": "RISK", **item.payload, "evidence_ids": [item.evidence_id]})
    return records


class DecisionNarrativeProvider:
    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings
        self.deterministic = ShelfCashDecisionIntelligenceAdapter()

    def explain(
        self,
        brief: DecisionBriefFacts,
        *,
        question: str | None,
        language: str,
        detail_level: str,
        semantic_facts: list[SemanticFact] | None = None,
        ingredient_id: str | None = None,
    ) -> DecisionExplanationResponse:
        # Preserve the existing human-readable deterministic fallback. Semantic
        # facts are machine evidence for retrieval/Qwen/grounding, not fallback prose.
        if ingredient_id:
            fallback = self.deterministic.explain_ingredient(
                brief, ingredient_id=ingredient_id, language=language,
                detail_level=detail_level, semantic_facts=semantic_facts or [],
            )
        else:
            fallback = self.deterministic.explain(
                brief, question=question, language=language, detail_level=detail_level,
            )
        if not self.llm_provider or not self.llm_provider.available:
            return fallback
        return self._qwen_or_fallback(
            brief, question, language, detail_level, fallback, semantic_facts,
            ingredient_id=ingredient_id,
        )

    def _qwen_or_fallback(
        self, brief, question, language, detail_level, fallback, semantic_facts,
        *, ingredient_id: str | None = None,
    ):
        started = time.monotonic()
        request_id = get_request_id()
        raw = None
        failure_stage = LLMFailureStage.UNKNOWN.value
        request_context: dict[str, Any] = {"decision_run_id": brief.decision_run_id}
        try:
            logger.info("decision_narrative_started request_id=%s decision_run_id=%s task=%s", request_id, brief.decision_run_id, LLMTask.DECISION_NARRATIVE.value)
            scoped_facts = (
                ingredient_scoped_semantic_facts(semantic_facts or [], ingredient_id)
                if ingredient_id else semantic_facts
            )
            evidence = (
                self.deterministic.ingredient_evidence(
                    brief, ingredient_id=ingredient_id, semantic_facts=scoped_facts or [],
                )
                if ingredient_id
                else self.deterministic._evidence(brief, semantic_facts=semantic_facts)
            )
            resolved_question = question or ("Why is this plan recommended?" if language == "en" else "Tại sao kế hoạch này được đề xuất?")
            if ingredient_id:
                # The API target is authoritative.  Do not let question-token
                # scoring select a different entity or omit target evidence.
                retrieved_items = evidence.items
                intent = _ingredient_intent(resolved_question)
            elif _requests_strategy_comparison(resolved_question):
                # Strategy questions need all persisted candidates and their
                # selected-relative deltas; token ranking must not drop one side.
                retrieved_items = [
                    item for item in evidence.items
                    if item.evidence_type in {
                        "semantic_strategy_candidate_metrics",
                        "semantic_strategy_comparison",
                        "semantic_strategy_selection_proof",
                    }
                ]
                intent = "STRATEGY_COMPARISON"
            else:
                retrieved = StructuredLocalRetriever().retrieve(resolved_question, evidence, DecisionGraph(request_id=brief.decision_run_id, nodes=[], edges=[]), context=build_retrieval_context(resolved_question, evidence, recommended_strategy=(brief.recommendation.strategy or "").upper() or None))
                retrieved_items = retrieved.items
                intent = retrieved.intent
            structured = aggregate_evidence(
                brief, retrieved_items, semantic_facts=scoped_facts,
                include_daily=not ingredient_id or _requests_daily_detail(resolved_question),
            )
            if not structured:
                raise ValueError("no_retrieved_evidence")
            logger.info("decision_narrative_retrieval_completed decision_run_id=%s evidence_count=%d intent=%s target_ingredient_id=%s", brief.decision_run_id, len(structured), intent, ingredient_id)
            payload = {"question": resolved_question, "language": language, "detail_level": detail_level, "evidence": structured}
            if ingredient_id:
                payload["target"] = {
                    "ingredient_name": _ingredient_display_name(brief, ingredient_id),
                    "scope": "one_ingredient_only",
                }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    raw = pool.submit(lambda: asyncio.run(self.llm_provider.generate_json(
                        SYSTEM_PROMPT, payload, task=LLMTask.DECISION_NARRATIVE,
                        request_context=request_context,
                    ))).result()
            else:
                raw = asyncio.run(self.llm_provider.generate_json(
                    SYSTEM_PROMPT, payload, task=LLMTask.DECISION_NARRATIVE,
                    request_context=request_context,
                ))

            logger.info("decision_narrative_qwen_completed request_id=%s decision_run_id=%s task=%s", request_id, brief.decision_run_id, LLMTask.DECISION_NARRATIVE.value)
            try:
                typed_raw = DecisionNarrativeLLMResponse.model_validate(raw)
            except PydanticValidationError as exc:
                failure_stage = LLMFailureStage.SCHEMA_VALIDATION.value
                raise ValueError("narrative_schema_validation_failed") from exc
            try:
                response = self._guard(
                    typed_raw.model_dump(mode="json"), structured, evidence.items, brief,
                    language, detail_level, intent, target_ingredient_id=ingredient_id,
                )
            except Exception:
                failure_stage = LLMFailureStage.GROUNDING.value
                raise
            logger.info("decision_narrative_grounding_passed request_id=%s decision_run_id=%s task=%s duration_ms=%d", request_id, brief.decision_run_id, LLMTask.DECISION_NARRATIVE.value, int((time.monotonic() - started) * 1000))
            return response.model_copy(update={
                "raw_response": request_context.get("openrouter_raw_content", raw),
                "llm_diagnostics": {
                    "status": "success",
                    "failure_stage": None,
                    "metadata": request_context.get("openrouter_metadata", {}),
                },
            })
        except Exception as exc:
            details = getattr(exc, "details", {})
            if failure_stage == LLMFailureStage.UNKNOWN.value and isinstance(details, dict):
                failure_stage = str(details.get("failure_stage") or failure_stage)
            details = details if isinstance(details, dict) else {}
            metadata = request_context.get("openrouter_metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            task_profile = getattr(self.llm_provider, "task_profile", None)
            profile = task_profile(LLMTask.DECISION_NARRATIVE) if callable(task_profile) else None
            configured_model = details.get("configured_model") or getattr(profile, "model", None)
            resolved_model = details.get("resolved_model") or metadata.get("resolved_model")
            resolved_provider = details.get("resolved_provider") or metadata.get("resolved_provider")
            logger.warning(
                "decision_narrative_failed request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s failure_stage=%s reason=%s",
                request_id, brief.decision_run_id, LLMTask.DECISION_NARRATIVE.value, configured_model, resolved_model,
                resolved_provider, failure_stage, type(exc).__name__,
            )
            logger.warning(
                "decision_narrative_fallback request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_provider=%s failure_stage=%s duration_ms=%d",
                request_id, brief.decision_run_id, LLMTask.DECISION_NARRATIVE.value, configured_model, resolved_provider,
                failure_stage, int((time.monotonic() - started) * 1000),
            )
            update_dict: dict[str, Any] = {
                "provider": "deterministic_fallback",
                "llm_diagnostics": {
                    "status": "failed",
                    "failure_stage": failure_stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "http_status": getattr(exc, "http_status", None),
                    "details": details,
                    "metadata": metadata,
                },
            }
            raw_content = request_context.get("openrouter_raw_content")
            if raw_content is not None:
                update_dict["raw_response"] = raw_content
            elif raw is not None:
                update_dict["raw_response"] = raw
            elif request_context.get("openrouter_raw_response") is not None:
                update_dict["raw_response"] = request_context["openrouter_raw_response"]
            else:
                update_dict["raw_response"] = {"failure_stage": failure_stage, "reason": type(exc).__name__}
            return fallback.model_copy(update=update_dict)

    def _guard(
        self, raw, structured, evidence_items, brief, language, detail_level, intent,
        *, target_ingredient_id: str | None = None,
    ):
        if not isinstance(raw.get("answer"), str) or not isinstance(raw.get("claims"), list):
            raise ValueError("malformed_qwen_output")
        by_id = {item.evidence_id: item for item in evidence_items}
        structured_by_id = {item["evidence_id"]: item for item in structured}
        allowed_ids = set(structured_by_id)
        if target_ingredient_id:
            for item in structured:
                item_ingredient_id = item.get("ingredient_id")
                if item_ingredient_id and item_ingredient_id != target_ingredient_id:
                    raise ValueError("target_evidence_entity_mismatch")
        claims = []
        citation_ids = set()
        used = raw.get("used_evidence_ids")
        if not isinstance(used, list) or not set(used) <= allowed_ids:
            raise ValueError("unsupported_used_evidence_id")
        claimed_evidence_ids = set()
        for claim in raw["claims"]:
            if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
                raise ValueError("malformed_claim")
            ids = claim.get("evidence_ids")
            if not isinstance(ids, list) or not ids or not set(ids) <= allowed_ids:
                raise ValueError("unsupported_evidence_id")
            claimed_evidence_ids.update(ids)
            supported_types = {structured_by_id[evidence_id]["type"] for evidence_id in ids}
            if claim.get("type") not in supported_types:
                raise ValueError("unsupported_claim_type")
            self._validate_numbers(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_entities(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids], brief)
            self._validate_target_entity(
                claim["text"], [structured_by_id[evidence_id] for evidence_id in ids],
                brief, target_ingredient_id,
            )
            self._validate_supported_concepts(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_causal_language(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_strategy_selection_language(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_baseline_language(claim["text"], [structured_by_id[evidence_id] for evidence_id in ids])
            self._validate_public_text(claim["text"])
            claim_source_ids = sorted({source_id for evidence_id in ids for source_id in structured_by_id[evidence_id].get("evidence_ids", [evidence_id])})
            claims.append(ExplanationClaim(type=claim["type"], value=claim["text"], evidence_ids=claim_source_ids))
            for evidence_id in ids:
                citation_ids.update(structured_by_id[evidence_id].get("evidence_ids", [evidence_id]))
        if set(used) != claimed_evidence_ids:
            raise ValueError("used_evidence_ids_mismatch")
        self._validate_public_text(raw["answer"])
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
    def _validate_target_entity(text: str, items: list[dict], brief, target_ingredient_id: str | None):
        if not target_ingredient_id:
            return
        cited_ids = {item.get("ingredient_id") for item in items if item.get("ingredient_id")}
        known_names = {
            row.ingredient_name.lower(): row.ingredient_id
            for row in [*brief.ingredient_demand, *brief.procurement_rows]
            if row.ingredient_name
        }
        lowered = text.lower()
        if any(name in lowered and ingredient_id != target_ingredient_id for name, ingredient_id in known_names.items()):
            raise ValueError("target_entity_switched")
        target_name = next((name for name, value in known_names.items() if value == target_ingredient_id), None)
        if target_name and target_name in lowered and target_ingredient_id not in cited_ids:
            raise ValueError("target_claim_requires_target_evidence")

    @staticmethod
    def _validate_baseline_language(text: str, items: list[dict]):
        if not any(item.get("type") == "NO_PLANNED_PURCHASE_BASELINE" for item in items):
            return
        if not any(item.get("existing_inbound_retained") is True for item in items):
            return
        lowered = text.lower()
        prohibited = (
            "kh\u00f4ng c\u00f3 b\u1ea5t k\u1ef3 h\u00e0ng nh\u1eadp n\u00e0o",
            "kh\u00f4ng c\u00f3 h\u00e0ng nh\u1eadp v\u1ec1",
            "no inbound",
            "no incoming stock",
        )
        if any(phrase in lowered for phrase in prohibited):
            raise ValueError("baseline_inbound_semantics_contradicted")

    @staticmethod
    def _validate_public_text(text: str):
        """Machine codes are evidence identifiers, never manager-facing prose."""
        if re.search(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", text):
            raise ValueError("raw_machine_code_in_narrative")

    @staticmethod
    def _validate_supported_concepts(text: str, items: list[dict]):
        lowered = text.lower()
        required = {
            ("tồn an toàn", "safety stock"): lambda: any(item.get("code") == "SAFETY_STOCK_PROTECTION" for item in items),
            ("moq", "đặt tối thiểu"): lambda: any(item["type"] == "MOQ" or item.get("code") == "MOQ_CONSTRAINT" for item in items),
            ("hạn dùng", "hết hạn"): lambda: any(item["type"] == "EXPIRY" or item.get("code") == "EXPIRING_INVENTORY" for item in items),
            ("lead time", "thời gian giao"): lambda: any(item["type"] == "LEAD_TIME" or item.get("code") == "LEAD_TIME_PRESSURE" for item in items),
            ("ngân sách", "budget"): lambda: any(item["type"] == "BUDGET" or item.get("code") == "BUDGET_CONSTRAINT" for item in items),
            ("rủi ro", "xác suất thiếu"): lambda: any(
                item["type"] == "RISK"
                or item.get("code") == "STOCKOUT_RISK"
                or (
                    item["type"] in {"STRATEGY_CANDIDATE_METRICS", "STRATEGY_COMPARISON"}
                    and (item.get("stockout_probability") is not None or item.get("stockout_probability_delta") is not None)
                )
                for item in items
            ),
        }
        for phrases, is_supported in required.items():
            if any(phrase in lowered for phrase in phrases) and not is_supported():
                raise ValueError("unsupported_causal_concept")

    @staticmethod
    def _validate_causal_language(text: str, items: list[dict]):
        lowered = f" {text.lower()} "
        causal_markers = (
            " vÃ¬ ", " do ", " nÃªn ", " dáº«n Ä‘áº¿n ", " Ä‘á»ƒ trÃ¡nh ",
            " because ", " due to ", " therefore ", " caused by ",
        )
        # Keep the Vietnamese markers ASCII-escaped: this file contains
        # historical mojibake literals, while model output is UTF-8 text.
        causal_markers = (
            " v\u00ec ", " do ", " n\u00ean ", " d\u1eabn \u0111\u1ebfn ",
            " \u0111\u1ec3 tr\u00e1nh ", " because ", " due to ", " therefore ", " caused by ",
        )
        if not any(marker in lowered for marker in causal_markers):
            return
        if any(item.get("classification") == "CAUSAL" or item.get("type") == "PROCUREMENT_REASON" for item in items):
            return
        raise ValueError("unsupported_causal_claim")

    @staticmethod
    def _validate_strategy_selection_language(text: str, items: list[dict]):
        """Selection claims may use only the persisted cost-based proof."""
        if not any(item.get("type") == "STRATEGY_SELECTION_PROOF" for item in items):
            return
        lowered = text.lower()
        forbidden = (
            "fill rate cao nhất", "mức đáp ứng cao nhất", "rủi ro thấp nhất",
            "an toàn nhất", "tốt nhất", "optimal", "safest", "highest fill",
        )
        if any(phrase in lowered for phrase in forbidden):
            raise ValueError("unsupported_strategy_selection_reason")
        if any(marker in f" {lowered} " for marker in (" vì ", " do ", " because ", " due to ")):
            if not any(marker in lowered for marker in ("chi phí", "purchase cost", "cost")):
                raise ValueError("selection_reason_missing_persisted_metric")


def _ingredient_display_name(brief: DecisionBriefFacts, ingredient_id: str) -> str:
    for row in [*brief.ingredient_demand, *brief.procurement_rows]:
        if row.ingredient_id == ingredient_id and row.ingredient_name:
            return row.ingredient_name
    return ingredient_id


def _ingredient_intent(question: str) -> str:
    lowered = question.lower()
    if any(token in lowered for token in ("n\u1ebfu kh\u00f4ng", "kh\u00f4ng nh\u1eadp", "without purchase", "without ordering")):
        return "INGREDIENT_BASELINE"
    if any(token in lowered for token in ("bao nhi\u00eau", "l\u01b0\u1ee3ng", "quantity", "30 kg")):
        return "INGREDIENT_QUANTITY"
    if any(token in lowered for token in ("nhu c\u1ea7u", "peak", "demand")):
        return "INGREDIENT_DEMAND"
    if any(token in lowered for token in ("v\u00ec sao", "t\u1ea1i sao", "why", "c\u1ea7n nh\u1eadp")):
        return "INGREDIENT_NEED"
    return "EXPLAIN_INGREDIENT_PROCUREMENT"


def _requests_daily_detail(question: str) -> bool:
    lowered = question.lower()
    return any(token in lowered for token in ("t\u1eebng ng\u00e0y", "ng\u00e0y n\u00e0o", "daily", "peak"))


def _requests_strategy_comparison(question: str) -> bool:
    lowered = question.lower()
    return any(token in lowered for token in (
        "chi\u1ebfn l\u01b0\u1ee3c", "strategy", "protected", "balanced", "lean",
        "an to\u00e0n", "c\u00e2n b\u1eb1ng", "ti\u1ebft ki\u1ec7m",
    ))
