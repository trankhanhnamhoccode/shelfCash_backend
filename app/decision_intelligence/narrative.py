"""Read-only, grounded natural-language narration for persisted decision facts."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from app.core.logging_context import get_request_id
from app.decision_intelligence.adapter import (
    ShelfCashDecisionIntelligenceAdapter,
    ingredient_scoped_semantic_facts,
)
from app.decision_intelligence.contracts import Citation, ConversationalExplanationLLMResponse, DecisionBriefFacts, DecisionExplanationResponse, ExplanationClaim
from app.decision_intelligence.semantic_state import validate_semantic_state
from app.decision_intelligence.causal_assertion import asserts_cause, is_causal_limitation
from app.decision_intelligence.communication_plan import narrative_communication_plan
from app.decision_intelligence.display import add_numeric_display_contract, purchase_cost_display
from app.decision_intelligence.narrative_retrieval import detect_intent, retrieve_narrative_evidence
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder, SemanticFact
from app.decision_intelligence.style_examples import retrieve_style_examples
from app.decision_intelligence.numeric_authority import (
    NumericKind,
    build_numeric_authority,
    equivalent_or_rounded,
    iter_numeric_mentions,
    parse_numeric_mention,
    render_fact,
)
from app.llm.tasks import LLMFailureStage, LLMTask
from app.llm.runtime import generate_json_sync
from app.services.decision.explanation_strategy_aliases import mentioned_strategies

logger = logging.getLogger("shelfcash.decision_narrative")

_APPROX_WORDS = ("khoảng", "xấp xỉ", "gần", "approximately", "about", "around")

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

STYLE / ANSWER-FIRST:

Khi có đủ evidence để trả lời, trả lời trực tiếp ngay ở câu đầu. Không mở đầu bằng
"Theo thông tin được cung cấp", "Dữ liệu cho thấy", "Hệ thống ghi nhận" hoặc
"Dựa trên kết quả". Hãy viết như trợ lý vận hành brief nhanh: "Kế hoạch đề xuất
nhập...", "Ngày cần chú ý là...", "... có thể thiếu từ...".

Nếu COMMUNICATION_PLAN được cung cấp, answer_with/decision là facts trả lời chính
và phải được ưu tiên; supporting chỉ dùng khi cần. Khi hỏi tại sao/vì sao, chỉ trả
lời nguyên nhân ngay câu đầu nếu answer_with có PROCUREMENT_REASON hoặc CAUSAL
fact. Nếu không có causal fact, giữ nguyên câu "Chưa đủ dữ liệu để xác nhận nguyên
nhân này." Không đọc JSON thành câu, không lặp cùng số liệu, và với
target.scope=one_ingredient_only chỉ nói nguyên liệu đó.

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

SYSTEM_PROMPT += """
SLICE F QUESTION CONTRACT:
Answer the user's QUESTION first; do not merely summarize the easiest evidence.
Use COMMUNICATION_PLAN.answer_with as the primary answer facts.
For PLAN_SELECTION, name the selected strategy first and state "selected because"
only with STRATEGY_SELECTION_PROOF. For comparisons/trade-offs, state only
supplied directional relations; never calculate, rank, or turn comparison into
rejection causality. If proof is absent, say that the selection reason cannot
be confirmed. ENTITY_OPERATIONAL stays within the resolved ingredient.
GENERAL_DECISION uses only relevant selected-plan facts, explicit risks, or
limitations. Do not expose UUIDs, evidence IDs, or internal implementation
terms. Use numbers only when material and copy authorized display tokens exactly:
never round, convert units, or derive values. Keep claims granular, with each
material premise mapped to its evidence. Simple answers are 1-3 concise sentences;
manager may add a supported trade-off/risk; technical may add user-facing
provenance detail without internal codes.


STYLE_EXAMPLES ARE NOT EVIDENCE. They only demonstrate tone and sentence structure.
Never take a number, date, ingredient, supplier, strategy, cause, or factual claim from a
style example. Never cite an example ID. If an example conflicts with EVIDENCE, EVIDENCE wins.
COMMUNICATION_PLAN.causal_allowed is authoritative: when false, use no causal connector or
causal hedge. Every factual claim must be grounded only in EVIDENCE.
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
            ingredient_name = next((row.ingredient_name for row in brief.procurement_rows if row.ingredient_id == item.entities.get("ingredient_id") and row.ingredient_name), None)
            records.append({
                "evidence_id": item.evidence_id,
                "type": "PROCUREMENT_QUANTITY",
                "ingredient_id": item.entities.get("ingredient_id"),
                "ingredient_name": ingredient_name,
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
        elif fact.fact_type != "PROCUREMENT_QUANTITY":
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
    return [add_numeric_display_contract(record) for record in records]


CONVERSATIONAL_SYSTEM_PROMPT = """You are ShelfCash's operations assistant. Answer the store owner's current question directly and naturally.

Use only the current BUSINESS_BRIEF for business facts and numbers. Recent history is conversational context only: never reuse an old number when it conflicts with the current brief. Do not calculate forecast, procurement, strategy, risk, or feasibility. If the brief is insufficient, say so. Be concise for the requested detail level.

Return the required JSON object with only `answer`."""


def _business_brief(selected: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Render selected grounded evidence as small, human-readable fact cards.

    Validation still receives the original structured evidence server-side. This
    presentation intentionally excludes numeric guard metadata and internal
    provenance so Qwen sees a compact brief, not a Decision Package dump.
    """
    cards: list[dict[str, str]] = []
    for item in selected:
        values = item.get("values") if isinstance(item.get("values"), dict) else item
        fact_type = str(item.get("type") or "FACT")
        if fact_type == "BUDGET_STATUS":
            if values.get("availability") != "available":
                text = f"Budget data availability: {values.get('availability')}; an exceeded/not-exceeded conclusion is unavailable."
            else:
                currency = values.get("currency") or "VND"
                text = (
                    f"Budget data available. The plan {'exceeds' if values.get('exceeds_budget') else 'does not exceed'} "
                    f"the budget. Budget limit: {values.get('budget_limit')} {currency}; "
                    f"planned spend: {values.get('planned_spend')} {currency}; "
                    f"over by: {values.get('over_by')} {currency}; "
                    f"remaining: {values.get('remaining_budget')}; utilization: {values.get('budget_utilization_pct')}%; currency: {currency}."
                )
        elif fact_type == "PROCUREMENT_QUANTITY":
            quantity = (item.get("display_values") or {}).get("value", values.get("value"))
            text = f"Recommendation: the current plan proposes buying {quantity} {values.get('unit')} {values.get('ingredient_name')}. This is a proposed purchase, not an existing order."
        elif fact_type == "DEMAND_HORIZON_SUMMARY":
            p50 = (item.get("display_values") or {}).get("p50_total", values.get("p50_total"))
            text = f"Demand observation: p50 demand over the horizon is {p50} {values.get('unit')} for {values.get('ingredient_name')}. This observation alone does not explain the purchase quantity."
        elif fact_type == "DEMAND_ORDER_ALIGNMENT":
            gap = values.get("absolute_gap")
            direction = "below" if isinstance(gap, (int, float)) and gap < 0 else "above" if isinstance(gap, (int, float)) and gap > 0 else "equal to"
            magnitude = (item.get("display_values") or {}).get("absolute_gap_magnitude", values.get("absolute_gap_magnitude"))
            text = f"Derived comparison: planned purchase is {direction} p50 demand by {magnitude} {values.get('unit')}. This gap is not a shortage or a procurement reason."
        elif fact_type == "NO_PLANNED_PURCHASE_BASELINE":
            display = item.get("display_values") or {}
            shortage = display.get("shortage_quantity", values.get("shortage_quantity"))
            fill_rate = display.get("fill_rate")
            stockout = display.get("projected_stockout_date", values.get("projected_stockout_date"))
            text = f"No-purchase baseline: excluding planned purchases from this Decision Run (existing inbound retained), simulated shortage is {shortage} {values.get('unit')}."
            if fill_rate is not None:
                text += f" Fill rate: {fill_rate}."
            if stockout:
                text += f" Projected stockout date: {stockout}."
            text += " This is a baseline scenario, not selected-plan shortage."
        elif fact_type == "PROCUREMENT_REASON":
            text = f"Authoritative procurement reason: {values.get('meaning') or values.get('reason')}."
        elif fact_type == "PLAN_OVERVIEW":
            text = f"Selected plan: {values.get('strategy')}; recommendation available: {values.get('available')}."
        elif fact_type == "STRATEGY_SELECTION_PROOF":
            text = f"Verified selection rule: {values.get('rule')}; selected: {values.get('selected_strategy')}; eligible candidates: {values.get('eligible_strategies')}. Only this proof authorizes a selection cause."
        elif fact_type == "STRATEGY_COMPARISON":
            entities = item.get("entities") if isinstance(item.get("entities"), dict) else values
            deltas = "; ".join(f"{key}: {value}" for key, value in values.items() if value is not None and key.endswith("_delta"))
            text = f"Comparison {entities.get('left_strategy')} vs {entities.get('right_strategy')}. {deltas or 'No authoritative delta is available.'}"
        elif fact_type == "STRATEGY_CANDIDATE_METRICS":
            entities = item.get("entities") if isinstance(item.get("entities"), dict) else values
            useful = "; ".join(f"{key}: {value}" for key, value in values.items() if value is not None and key in {"purchase_cost", "fill_rate", "stockout_probability", "shortage_quantity", "feasible"})
            text = f"Strategy {entities.get('strategy')}: {useful}."
        elif fact_type in {"SELECTED_PLAN_RISK_METRICS", "RISK"}:
            display = item.get("display_values") or {}
            metrics = []
            if isinstance(values.get("shortage_quantity"), (int, float)) and values["shortage_quantity"] > 0:
                metrics.append(f"projected shortage: {display.get('shortage_quantity', values['shortage_quantity'])}")
            if isinstance(values.get("stockout_probability"), (int, float)) and values["stockout_probability"] > 0:
                metrics.append(f"stockout probability: {display.get('stockout_probability', values['stockout_probability'])}")
            if isinstance(values.get("expected_fill_rate"), (int, float)) and values["expected_fill_rate"] < 1:
                metrics.append(f"expected demand fill rate: {display.get('expected_fill_rate', values['expected_fill_rate'])}")
            text = f"Selected-plan risk observation: {'; '.join(metrics)}. These are projected metrics, not an actual stockout."
        elif fact_type == "INGREDIENT_OPERATIONAL_RISK":
            display = item.get("display_values") or {}
            basis = "conservative design scenario" if values.get("basis_kind") == "conservative_design_scenario" else "persisted scenario"
            shortage = display.get("shortage_quantity", values.get("shortage_quantity"))
            detail = (f"simulated shortage {shortage} {values.get('unit') or ''}" if isinstance(values.get("shortage_quantity"), (int, float)) and values["shortage_quantity"] > 0 else
                      f"projected stockout date {display.get('first_stockout_date', values.get('first_stockout_date'))}" if values.get("first_stockout_date") else
                      "a projected stockout event")
            text = f"Operational risk observation in the {basis}: {detail} for {values.get('ingredient_name') or 'an ingredient'}. This is not an actual stockout."
        elif fact_type == "STRESS_SHORTAGE_OBSERVED":
            display = item.get("display_values") or {}
            shortage = values.get("shortage_quantity")
            text = (f"Stress-scenario risk observation: simulated shortage {display.get('shortage_quantity', shortage)} {values.get('unit') or ''}. This is not selected-plan or actual shortage."
                    if isinstance(shortage, (int, float)) and shortage > 0 else
                    "Stress-scenario warning: shortage was observed in a stress simulation. No shortage quantity is supplied by this warning code.")
        elif fact_type == "STRESS_CAPACITY_VIOLATION":
            display = item.get("display_values") or {}
            amount = values.get("capacity_violation_quantity")
            text = (f"Stress-scenario capacity observation: simulated violation {display.get('capacity_violation_quantity', amount)} {values.get('unit') or ''}. This is not a selected-plan or actual capacity violation."
                    if isinstance(amount, (int, float)) and amount > 0 else
                    "Stress-scenario warning: capacity violation was observed in a stress simulation. No quantity is supplied by this warning code.")
        else:
            display = item.get("display_values") if isinstance(item.get("display_values"), dict) else values
            concise = "; ".join(f"{key}: {value}" for key, value in display.items() if value is not None)
            text = f"{fact_type}: {concise or 'available as supplied.'}"
        cards.append({"fact_id": str(item["evidence_id"]), "evidence_id": str(item["evidence_id"]), "type": fact_type, "text": text})
    return cards


def _turn_hint(intent: str) -> str:
    hints = {
        "BUDGET": "Answer the budget status and state the overage or remaining amount when available.",
        "STRATEGY_COMPARISON": "Explain only the requested strategy trade-off from the supplied comparison facts.",
        "PLAN_STRATEGY": "Explain the selected strategy and relevant trade-offs.",
        "WHY_PROCUREMENT": "Use an authoritative procurement reason only if supplied. Otherwise describe the recommendation, demand, derived comparison, and baseline as separate facts; say the exact cause is unavailable.",
        "BASELINE": "Answer the no-purchase scenario directly from the baseline card. Keep it distinct from the selected plan.",
        "RISK": "Answer with the direct risk observation first. Distinguish selected-plan projections from stress or conservative scenarios; never describe simulated stockout as already occurring.",
    }
    return hints.get(intent, "Answer the user's question from the supplied facts without summarizing the whole plan.")


class _ConversationalPayload(dict):
    """Keep legacy in-process test doubles readable without serializing raw evidence.

    The compatibility accessor is deliberately not a dictionary key, so JSON
    transport to OpenRouter contains only the compact `business_brief`.
    """
    def __init__(self, payload: dict[str, Any], *, legacy_evidence: list[dict[str, Any]], legacy_target: dict[str, str] | None):
        super().__init__(payload)
        self._legacy_evidence = legacy_evidence
        self._legacy_target = legacy_target

    def __getitem__(self, key):
        if key == "evidence":
            return self._legacy_evidence
        if key == "target" and self._legacy_target is not None:
            return self._legacy_target
        return super().__getitem__(key)


def _fallback_with_provenance(fallback, lines: list[tuple[str, dict]], evidence_items: list):
    """Publish only sentences whose selected fact has a public citation."""
    by_id = {item.evidence_id: item for item in evidence_items}
    supported: list[str] = []
    claims: list[ExplanationClaim] = []
    cited: dict[str, Citation] = {}
    for sentence, record in lines:
        if not record:
            continue
        source_ids = list(dict.fromkeys(record.get("evidence_ids") or [record.get("evidence_id")]))
        if not source_ids or any(source_id not in by_id for source_id in source_ids):
            continue
        supported.append(sentence)
        claims.append(ExplanationClaim(type=str(record.get("type") or "FACT"), value=sentence, evidence_ids=source_ids))
        for source_id in source_ids:
            item = by_id[source_id]
            cited[source_id] = Citation(evidence_id=source_id, label=item.text, source_type=item.source_object)
    if not supported:
        return fallback
    answer = " ".join(supported)
    return fallback.model_copy(update={
        "summary": answer, "answer": answer, "why_this_plan": supported,
        "claims": claims, "citations": list(cited.values()), "grounded": True,
    })


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
        question_scope: str | None = None,
        history: list[Any] | None = None,
    ) -> DecisionExplanationResponse:
        # Preserve the existing human-readable deterministic fallback. Semantic
        # facts are machine evidence for retrieval/Qwen/grounding, not fallback prose.
        if ingredient_id:
            fallback = self.deterministic.explain_ingredient(
                brief, ingredient_id=ingredient_id, language=language,
                detail_level=detail_level, semantic_facts=semantic_facts or [], question=question,
            )
        else:
            fallback = self.deterministic.explain(
                brief, question=question, language=language, detail_level=detail_level,
            )
        if question_scope == "plan_strategy":
            records = self._selected_semantic_records(brief, question, detail_level, semantic_facts, question_scope)
            evidence_items = self.deterministic._evidence(brief, semantic_facts=semantic_facts).items
            fallback = self._semantic_plan_fallback(fallback, records, language, evidence_items)
        elif question_scope == "budget":
            records = self._selected_semantic_records(brief, question, detail_level, semantic_facts, question_scope)
            evidence_items = self.deterministic._evidence(brief, semantic_facts=semantic_facts).items
            fallback = self._semantic_budget_fallback(fallback, records, language, evidence_items)
        elif question and detect_intent(question) == "RISK":
            records = self._selected_semantic_records(brief, question, detail_level, semantic_facts, question_scope)
            evidence_items = self.deterministic._evidence(brief, semantic_facts=semantic_facts).items
            fallback = self._semantic_risk_fallback(fallback, records, language, evidence_items)
        if not self.llm_provider or not self.llm_provider.available:
            return fallback
        return self._qwen_or_fallback(
            brief, question, language, detail_level, fallback, semantic_facts,
            ingredient_id=ingredient_id, question_scope=question_scope, history=history,
        )

    def _qwen_or_fallback(
        self, brief, question, language, detail_level, fallback, semantic_facts,
        *, ingredient_id: str | None = None, question_scope: str | None = None, history: list[Any] | None = None,
    ):
        started = time.monotonic()
        request_id = get_request_id()
        raw = None
        failure_stage = LLMFailureStage.UNKNOWN.value
        request_context: dict[str, Any] = {"decision_run_id": brief.decision_run_id}
        try:
            logger.info("decision_narrative_started request_id=%s decision_run_id=%s task=%s", request_id, brief.decision_run_id, LLMTask.CONVERSATIONAL_EXPLANATION.value)
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
            # Build canonical records locally, then select by intent/type/entity
            # before Qwen sees them. Retrieval is deterministic business logic,
            # not model-scored semantic similarity.
            all_structured = aggregate_evidence(
                brief, evidence.items, semantic_facts=scoped_facts,
                include_daily=True,
            )
            retrieval = retrieve_narrative_evidence(
                brief, all_structured, question=resolved_question,
                ingredient_id=ingredient_id, detail_level=detail_level, question_scope=question_scope,
            )
            structured = retrieval.evidence
            intent = retrieval.intent
            if not structured:
                raise ValueError("no_retrieved_evidence")
            logger.info(
                "decision_narrative_retrieval_completed decision_run_id=%s intent=%s target_ingredient_id=%s evidence=%s",
                brief.decision_run_id, intent, retrieval.target_ingredient_id,
                [(item["evidence_id"], item["type"]) for item in structured],
            )
            plan = narrative_communication_plan(structured, str(intent))
            selected_ids = set(plan.evidence_ids)
            selected = [item for item in structured if item["evidence_id"] in selected_ids]
            if question_scope == "plan_strategy":
                fallback = self._semantic_plan_fallback(fallback, selected, language, evidence.items)
            elif question_scope == "budget":
                fallback = self._semantic_budget_fallback(fallback, selected, language, evidence.items)
            elif intent == "RISK":
                fallback = self._semantic_risk_fallback(fallback, selected, language, evidence.items)
            payload = _ConversationalPayload({
                "question": resolved_question,
                "task_hint": _turn_hint(str(intent)),
                "business_brief": _business_brief(selected),
                "recent_history": [
                    {"role": str(turn.role), "content": str(turn.content)}
                    for turn in (history or [])
                ],
            }, legacy_evidence=selected, legacy_target=(
                {"ingredient_name": _ingredient_display_name(brief, retrieval.target_ingredient_id), "scope": "one_ingredient_only"}
                if retrieval.target_ingredient_id else None
            ))
            logger.info(
                "decision_narrative_communication_plan decision_run_id=%s intent=%s answer_with=%s attention=%s limitation=%s supporting=%s",
                brief.decision_run_id, intent, plan.decision, plan.main_attention, plan.limitation, plan.supporting,
            )
            raw = generate_json_sync(
                self.llm_provider, CONVERSATIONAL_SYSTEM_PROMPT, payload,
                task=LLMTask.CONVERSATIONAL_EXPLANATION,
                request_context=request_context,
            )

            logger.info("decision_narrative_qwen_completed request_id=%s decision_run_id=%s task=%s", request_id, brief.decision_run_id, LLMTask.CONVERSATIONAL_EXPLANATION.value)
            try:
                # Existing in-process integrations may still send the retired
                # bookkeeping fields. They are ignored only as a bounded
                # compatibility adapter; OpenRouter's strict task schema now
                # emits just `answer`, and no legacy field reaches grounding.
                parseable_raw = raw
                if (
                    isinstance(raw, dict)
                    and set(raw) <= {"answer", "claims", "used_evidence_ids"}
                    and isinstance(raw.get("claims"), list)
                    and raw["claims"]
                ):
                    parseable_raw = {"answer": raw.get("answer")}
                typed_raw = ConversationalExplanationLLMResponse.model_validate(parseable_raw)
            except PydanticValidationError as exc:
                failure_stage = LLMFailureStage.SCHEMA_VALIDATION.value
                raise ValueError("narrative_schema_validation_failed") from exc
            try:
                response = self._guard(
                    typed_raw.model_dump(mode="json"), selected, evidence.items, brief,
                    language, detail_level, intent, target_ingredient_id=retrieval.target_ingredient_id,
                )
            except Exception:
                failure_stage = LLMFailureStage.GROUNDING.value
                raise
            logger.info("decision_narrative_grounding_passed request_id=%s decision_run_id=%s task=%s duration_ms=%d", request_id, brief.decision_run_id, LLMTask.CONVERSATIONAL_EXPLANATION.value, int((time.monotonic() - started) * 1000))
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
            profile = task_profile(LLMTask.CONVERSATIONAL_EXPLANATION) if callable(task_profile) else None
            configured_model = details.get("configured_model") or getattr(profile, "model", None)
            resolved_model = details.get("resolved_model") or metadata.get("resolved_model")
            resolved_provider = details.get("resolved_provider") or metadata.get("resolved_provider")
            logger.warning(
                "decision_narrative_failed request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s failure_stage=%s reason=%s",
                request_id, brief.decision_run_id, LLMTask.CONVERSATIONAL_EXPLANATION.value, configured_model, resolved_model,
                resolved_provider, failure_stage, f"{type(exc).__name__}:{exc}",
            )
            logger.warning(
                "decision_narrative_fallback request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_provider=%s failure_stage=%s duration_ms=%d",
                request_id, brief.decision_run_id, LLMTask.CONVERSATIONAL_EXPLANATION.value, configured_model, resolved_provider,
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

    def _selected_semantic_records(self, brief, question, detail_level, semantic_facts, question_scope):
        evidence = self.deterministic._evidence(brief, semantic_facts=semantic_facts)
        records = aggregate_evidence(brief, evidence.items, semantic_facts=semantic_facts, include_daily=True)
        retrieval = retrieve_narrative_evidence(
            brief, records, question=question or "", ingredient_id=None,
            detail_level=detail_level, question_scope=question_scope,
        )
        return retrieval.evidence

    @staticmethod
    def _semantic_plan_fallback(fallback, records: list[dict], language: str, evidence_items: list):
        """Render only already-materialized strategy facts; never calculate."""
        overview = next((item for item in records if item.get("type") == "PLAN_OVERVIEW"), None)
        selected = str((overview or {}).get("strategy") or "").upper()
        proof = next((item for item in records if item.get("type") == "STRATEGY_SELECTION_PROOF"), None)
        comparisons = [item for item in records if item.get("type") == "STRATEGY_COMPARISON"]
        lines: list[tuple[str, dict]] = []
        if proof and proof.get("rule") == "lowest_valid_candidate_cost_then_strategy_name":
            lines.append((f"{selected} được chọn vì đây là phương án có chi phí mua thấp nhất trong các phương án đủ điều kiện.", proof))
        elif selected:
            lines.append((f"{selected} là phương án được chọn, nhưng dữ liệu Decision Run hiện không đủ để xác nhận lý do lựa chọn.", overview))
        else:
            return fallback
        for item in comparisons[:2]:
            left, right = str(item.get("left_strategy", "")).upper(), str(item.get("right_strategy", "")).upper()
            if isinstance(item.get("purchase_cost_delta"), (int, float)) and item["purchase_cost_delta"] < 0:
                lines.append((f"{left} có chi phí mua thấp hơn {right}.", item))
            elif isinstance(item.get("stockout_probability_delta"), (int, float)) and item["stockout_probability_delta"] < 0:
                lines.append((f"{left} có xác suất thiếu hàng thấp hơn {right}.", item))
        return _fallback_with_provenance(fallback, lines, evidence_items)

    @staticmethod
    def _semantic_budget_fallback(fallback, records: list[dict], language: str, evidence_items: list):
        """Answer budget questions from the persisted snapshot, never live settings."""
        budget = next((item for item in records if item.get("type") == "BUDGET_STATUS"), None)
        if not budget or budget.get("availability") != "available":
            answer = (
                "This Decision Run does not contain enough budget information to determine whether the plan exceeds its budget."
                if language == "en" else
                "Decision Run này không có đủ thông tin ngân sách để xác định kế hoạch có vượt ngân sách hay không."
            )
        else:
            limit = purchase_cost_display(budget.get("budget_limit")) or str(budget.get("budget_limit"))
            spend = purchase_cost_display(budget.get("planned_spend")) or str(budget.get("planned_spend"))
            if budget.get("exceeds_budget"):
                over_by = purchase_cost_display(budget.get("over_by")) or str(budget.get("over_by"))
                answer = (
                    f"The plan is expected to spend {spend} against a budget of {limit}, exceeding it by about {over_by}."
                    if language == "en" else
                    f"Kế hoạch dự kiến chi {spend} trên ngân sách {limit}, tức vượt khoảng {over_by}."
                )
            else:
                remaining = purchase_cost_display(budget.get("remaining_budget")) or str(budget.get("remaining_budget"))
                answer = (
                    f"The plan is expected to spend {spend} against a budget of {limit}, leaving about {remaining}."
                    if language == "en" else
                    f"Kế hoạch dự kiến chi {spend} trên ngân sách {limit}, còn khoảng {remaining}."
                )
            utilization = budget.get("budget_utilization_pct")
            if isinstance(utilization, (int, float)):
                rendered = budget.get("display_values", {}).get("budget_utilization_pct")
                if rendered:
                    answer += f" Budget utilization is {rendered}." if language == "en" else f" Mức sử dụng ngân sách là {rendered}."
        return _fallback_with_provenance(fallback, [(answer, budget)] if budget else [], evidence_items)

    @staticmethod
    def _semantic_risk_fallback(fallback, records: list[dict], language: str, evidence_items: list):
        """Answer a risk question from one persisted scenario fact, never critic prose."""
        for item in records:
            kind = item.get("type")
            values = item.get("display_values") or {}
            quantity = item.get("shortage_quantity")
            rendered = values.get("shortage_quantity", quantity)
            unit = item.get("unit") or ""
            if kind in {"SELECTED_PLAN_RISK_METRICS", "RISK"} and isinstance(quantity, (int, float)) and quantity > 0:
                sentence = (f"The selected plan has a projected shortage of {rendered}." if language == "en" else
                            f"Kế hoạch được chọn có nguy cơ thiếu hàng theo mô phỏng, với lượng thiếu dự kiến {rendered}.")
            elif kind in {"SELECTED_PLAN_RISK_METRICS", "RISK"} and isinstance(item.get("stockout_probability"), (int, float)) and item["stockout_probability"] > 0:
                probability = values.get("stockout_probability", item["stockout_probability"])
                sentence = (f"The selected plan has a projected stockout probability of {probability}." if language == "en" else
                            f"Kế hoạch được chọn có xác suất thiếu hàng dự kiến {probability} theo dữ liệu mô phỏng.")
            elif kind in {"SELECTED_PLAN_RISK_METRICS", "RISK"} and isinstance(item.get("expected_fill_rate"), (int, float)) and item["expected_fill_rate"] < 1:
                fill_rate = values.get("expected_fill_rate", item["expected_fill_rate"])
                sentence = (f"The selected plan's expected demand fill rate is {fill_rate}." if language == "en" else
                            f"Tỷ lệ đáp ứng nhu cầu dự kiến của kế hoạch được chọn là {fill_rate}; đây là chỉ số cần theo dõi.")
            elif kind == "INGREDIENT_OPERATIONAL_RISK":
                basis = "kịch bản nhu cầu bảo thủ" if item.get("basis_kind") == "conservative_design_scenario" else "kịch bản mô phỏng đã lưu"
                name = item.get("ingredient_name") or "một nguyên liệu"
                if isinstance(quantity, (int, float)) and quantity > 0:
                    sentence = (f"The persisted scenario projects a shortage of {rendered} {unit} for {name}." if language == "en" else
                                f"Trong {basis}, mô phỏng ghi nhận nguy cơ thiếu hàng ở {name}: {rendered} {unit}.")
                else:
                    date = values.get("first_stockout_date", item.get("first_stockout_date"))
                    sentence = (f"The persisted scenario projects a stockout for {name}" + (f" on {date}." if date else ".") if language == "en" else
                                f"Trong {basis}, mô phỏng ghi nhận nguy cơ hết hàng ở {name}" + (f" từ {date}." if date else "."))
            elif kind == "STRESS_SHORTAGE_OBSERVED" and isinstance(quantity, (int, float)) and quantity > 0:
                sentence = (f"The stress scenario simulates a shortage of {rendered} {unit}." if language == "en" else
                            f"Trong kịch bản stress, mô phỏng ghi nhận nguy cơ thiếu hàng: {rendered} {unit}.")
            elif kind == "STRESS_SHORTAGE_OBSERVED":
                sentence = ("A stress-scenario warning reports possible shortage; no quantity is supplied." if language == "en" else
                            "Cảnh báo của kịch bản stress ghi nhận nguy cơ thiếu hàng; cảnh báo này không cung cấp lượng thiếu cụ thể.")
            elif kind == "STRESS_CAPACITY_VIOLATION":
                amount = item.get("capacity_violation_quantity")
                if isinstance(amount, (int, float)) and amount > 0:
                    rendered_amount = values.get("capacity_violation_quantity", amount)
                    sentence = (f"The stress scenario simulates a capacity violation of {rendered_amount} {unit}." if language == "en" else
                                f"Trong kịch bản stress, mô phỏng ghi nhận vi phạm sức chứa {rendered_amount} {unit}.")
                else:
                    sentence = ("A stress-scenario warning reports a capacity violation without a quantity." if language == "en" else
                                "Cảnh báo của kịch bản stress ghi nhận vi phạm sức chứa; cảnh báo này không cung cấp lượng cụ thể.")
            else:
                continue
            return _fallback_with_provenance(fallback, [(sentence, item)], evidence_items)
        message = ("This Decision Run does not contain enough evidence to identify the main risk." if language == "en" else
                   "Dữ liệu Decision Run hiện chưa đủ để xác định rủi ro chính.")
        return fallback.model_copy(update={
            "summary": message, "answer": message, "why_this_plan": [],
            "claims": [], "citations": [], "grounded": False,
        })

    def _guard(
        self, raw, structured, evidence_items, brief, language, detail_level, intent,
        *, target_ingredient_id: str | None = None,
    ):
        # Conversational Qwen supplies wording only.  It never declares claims,
        # evidence IDs, citations, grounding, or provider metadata.  The
        # selected retrieval scope remains the authority and is applied below
        # per sentence rather than trusting the whole answer wholesale.
        if not isinstance(raw.get("answer"), str) or not raw["answer"].strip():
            raise ValueError("malformed_qwen_output")
        if target_ingredient_id:
            for item in structured:
                item_ingredient_id = item.get("ingredient_id")
                if item_ingredient_id and item_ingredient_id != target_ingredient_id:
                    raise ValueError("target_evidence_entity_mismatch")
        legacy_scopes: dict[str, list[dict]] = {}
        # Direct internal callers from before CHAT-6 may exercise `_guard`
        # with the retired shape. Keep that test-only/backward-compatible
        # adapter separate from the conversational provider path above.
        if isinstance(raw.get("claims"), list) and raw["claims"]:
            by_id = {item["evidence_id"]: item for item in structured}
            for claim in raw["claims"]:
                if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
                    raise ValueError("malformed_claim")
                ids = claim.get("evidence_ids")
                if not isinstance(ids, list) or not ids or not set(ids) <= set(by_id):
                    raise ValueError("unsupported_evidence_id")
                items = [by_id[evidence_id] for evidence_id in ids]
                if claim.get("type") not in {item.get("type") for item in items}:
                    raise ValueError("unsupported_claim_type")
                self._validate_or_repair_fragment(claim["text"], items, brief, target_ingredient_id)
                legacy_scopes[claim["text"]] = items
        claims, citation_ids, accepted = [], set(), []
        for fragment in self._answer_fragments(raw["answer"]):
            items = legacy_scopes.get(fragment) or self._scope_fragment(fragment, structured, brief, target_ingredient_id)
            try:
                fragment, repaired = self._validate_or_repair_fragment(
                    fragment, items, brief, target_ingredient_id,
                )
                if intent == "RISK":
                    self._validate_risk_shortage_concept(fragment, items)
            except ValueError as exc:
                # CHAT-5 permits a single unsupported numeric sentence to be
                # removed when a coherent grounded answer remains. Other
                # grounding failures remain fail-closed.
                if str(exc).startswith("unsupported_numeric_claim"):
                    continue
                raise
            accepted.append(fragment)
            source_ids = sorted({source_id for item in items for source_id in item.get("evidence_ids", [item["evidence_id"]])})
            if source_ids:
                claims.append(ExplanationClaim(
                    type=str(items[0].get("type", "FACT")), value=fragment,
                    evidence_ids=source_ids,
                ))
                citation_ids.update(source_ids)
        answer = " ".join(accepted).strip()
        if not answer:
            raise ValueError("material_unsupported_numeric_answer")
        citations = [Citation(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object) for item in evidence_items if item.evidence_id in citation_ids]
        entities = {"ingredient_ids": sorted({item.entities["ingredient_id"] for item in evidence_items if item.evidence_id in citation_ids and item.entities.get("ingredient_id")}), "supplier_ids": sorted({item.entities["supplier_id"] for item in evidence_items if item.evidence_id in citation_ids and item.entities.get("supplier_id")})}
        return DecisionExplanationResponse(source="openrouter_qwen", language=language, detail_level=detail_level, summary=answer, why_this_plan=[answer], main_risks=brief.critic.warnings, tradeoffs=[], important_assumptions=["Narrative is grounded only in the persisted decision package."], decision_run_id=brief.decision_run_id, answer=answer, intent=str(intent).upper(), entities=entities, claims=claims, citations=citations, grounded=True, provider="openrouter_qwen", raw_response=raw)

    @staticmethod
    def _answer_fragments(answer: str) -> list[str]:
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", answer.strip()) if part.strip()]

    def _validate_or_repair_fragment(self, text, items, brief, target_ingredient_id):
        self._validate_public_text(text)
        try:
            self._validate_numbers(text, items)
        except ValueError as exc:
            if not str(exc).startswith("unsupported_numeric_claim"):
                raise
            text, repaired = self._repair_numeric_text(text, items)
            if not repaired:
                raise
            self._validate_numbers(text, items)
        self._validate_entities(text, items, brief)
        self._validate_target_entity(text, items, brief, target_ingredient_id)
        self._validate_supported_concepts(text, items)
        validate_semantic_state(text, items)
        self._validate_ingredient_semantics(text, items)
        self._validate_risk_provenance(text, items)
        self._validate_causal_language(text, items)
        self._validate_strategy_selection_language(text, items)
        self._validate_strategy_semantics(text, items)
        self._validate_baseline_language(text, items)
        return text, True

    @staticmethod
    def _scope_fragment(text, structured, brief, target_ingredient_id):
        """Narrow backend-selected evidence deterministically for one sentence.

        This is deliberately a scope filter, never a model-selected citation
        lookup and never nearest-number matching.  It leaves the retrieval
        scope intact when wording is genuinely generic.
        """
        lowered = text.casefold()
        candidates = list(structured)
        types: set[str] = set()
        if any(token in lowered for token in ("ngân sách", "budget", "vượt", "overage", "remaining")):
            types.add("BUDGET_STATUS")
        if any(token in lowered for token in ("nhập", "đặt", "order", "ordering", "procurement")):
            types.add("PROCUREMENT_QUANTITY")
        if mentioned_strategies(text) or any(token in lowered for token in ("cân bằng", "tiết kiệm", "an toàn", "strategy", "chiến lược", "fill rate", "mức đáp ứng", "purchase cost", "chi phí mua", "stockout")):
            if any(token in lowered for token in ("được chọn", "selected", "lowest", "thấp nhất", "cheapest")):
                if any(token in lowered for token in ("lowest", "thấp nhất", "cheapest")):
                    types.update({"STRATEGY_SELECTION_PROOF", "PLAN_OVERVIEW"})
                elif not any(token in lowered for token in ("than", "hơn", "so với", "compared")):
                    types.add("PLAN_OVERVIEW")
                else:
                    types.add("STRATEGY_COMPARISON")
            else:
                types.add("STRATEGY_COMPARISON")
        if asserts_cause(text):
            causal = [item for item in candidates if item.get("classification") == "CAUSAL" or item.get("type") == "PROCUREMENT_REASON"]
            if causal:
                candidates = causal
        if types:
            typed = [item for item in candidates if item.get("type") in types]
            if typed:
                candidates = typed
        if any(item.get("type") in {"SELECTED_PLAN_RISK_METRICS", "RISK", "INGREDIENT_OPERATIONAL_RISK", "STRESS_SHORTAGE_OBSERVED", "STRESS_CAPACITY_VIOLATION"} for item in candidates):
            normalized_risk = _semantic_normalize(text).replace("đ", "d")
            if any(marker in normalized_risk for marker in ("stress", "kiem tra suc chiu dung")):
                risk_scoped = [item for item in candidates if item.get("type") in {"STRESS_SHORTAGE_OBSERVED", "STRESS_CAPACITY_VIOLATION"}]
            elif any(marker in normalized_risk for marker in ("kich ban nhu cau bao thu", "conservative scenario")):
                risk_scoped = [item for item in candidates if item.get("type") == "INGREDIENT_OPERATIONAL_RISK" and item.get("basis_kind") == "conservative_design_scenario"]
            elif any(marker in normalized_risk for marker in ("ke hoach duoc chon", "ke hoach hien tai", "selected plan", "current plan")):
                risk_scoped = [item for item in candidates if item.get("type") in {"SELECTED_PLAN_RISK_METRICS", "RISK"}]
            else:
                risk_scoped = []
            if risk_scoped:
                candidates = risk_scoped
        normalized = _semantic_normalize(text)
        mentioned = mentioned_strategies(text)
        if len(mentioned) >= 2:
            pairwise = [
                item for item in candidates
                if item.get("type") != "STRATEGY_COMPARISON" or {
                    _semantic_normalize(item.get("left_strategy")),
                    _semantic_normalize(item.get("right_strategy")),
                } <= mentioned
            ]
            if pairwise:
                candidates = pairwise
        mentioned_ids = {
            row.ingredient_id for row in [*brief.ingredient_demand, *brief.procurement_rows]
            if row.ingredient_name and row.ingredient_name.casefold() in lowered
        }
        if target_ingredient_id:
            mentioned_ids.add(target_ingredient_id)
        if mentioned_ids:
            entity_items = [item for item in candidates if item.get("ingredient_id") in mentioned_ids]
            if entity_items:
                candidates = entity_items
        mentions = list(iter_numeric_mentions(text))
        if mentions:
            numeric_items = []
            for item in candidates:
                registry = build_numeric_authority([item])
                if any(DecisionNarrativeProvider._is_authorized_numeric(mention, registry, False) for mention in mentions):
                    numeric_items.append(item)
            if numeric_items:
                candidates = numeric_items
        return candidates

    @staticmethod
    def _validate_numbers(text: str, payloads: list[dict]):
        registry = build_numeric_authority(payloads)
        supplied_ranges = [
            str(value) for payload in payloads
            for value in (payload.get("display_values") or {}).values()
            if "–" in str(value)
        ]
        protected_text = text
        for supplied in supplied_ranges:
            protected_text = protected_text.replace(supplied, " ")
        # Dates are temporal evidence checked by the other grounding guards,
        # not scalar numeric claims (both ISO and manager-facing dd/mm forms).
        protected_text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b", " ", protected_text)
        if any(value in text for value in supplied_ranges):
            if any(marker in text.lower() for marker in ("trung bình", "average", "mean")):
                raise ValueError("range_semantics_contradicted")
        for mention in iter_numeric_mentions(protected_text):
            # ISO dates are backend-owned temporal tokens, not scalar claims.
            if re.fullmatch(r"\d{4}", mention.text) and text[mention.end:].startswith("-"):
                continue
            approximate = any(word in text[max(0, mention.start - 16):mention.start].lower() for word in _APPROX_WORDS)
            if not DecisionNarrativeProvider._is_authorized_numeric(mention, registry, approximate):
                raise ValueError("unsupported_numeric_claim")

    @staticmethod
    def _numeric_value(mention: str) -> tuple[Decimal | None, str]:
        parsed = parse_numeric_mention(mention)
        if parsed is None:
            return None, "count"
        value, kind, _unit = parsed
        return value, (kind.value if kind is not None else "count")

    @staticmethod
    def _is_authorized_numeric(mention, registry, approximate: bool) -> bool:
        return any(equivalent_or_rounded(mention, fact, approximate=approximate) for fact in registry)

    @staticmethod
    def _repair_numeric_text(text: str, payloads: list[dict]) -> tuple[str, int]:
        """Replace one clearly monetary hallucination with a backend display value.

        This deliberately repairs only a bounded local error. Multiple bad
        numbers remain a whole-answer grounding failure.
        """
        bad = []
        registry = build_numeric_authority(payloads)
        for mention in iter_numeric_mentions(text):
            if not DecisionNarrativeProvider._is_authorized_numeric(mention, registry, False):
                bad.append(mention)
        if len(bad) != 1:
            return text, 0
        mention = bad[0]
        candidates = [fact for fact in registry if mention.kind is None or fact.kind is mention.kind]
        if mention.kind is NumericKind.QUANTITY and mention.unit:
            candidates = [fact for fact in candidates if not fact.unit or fact.unit == mention.unit]
        context = text.casefold()
        key_hints = (
            (("vượt", "over by", "overage"), ("over_by",)),
            (("còn", "remaining", "left"), ("remaining_budget",)),
            (("dự kiến chi", "planned spend", "chi phí", "costs", "purchase cost"), ("planned_spend", "purchase_cost")),
            (("ngân sách", "budget limit", "budget cap"), ("budget_limit",)),
        )
        for markers, keys in key_hints:
            if any(marker in context for marker in markers):
                narrowed = [fact for fact in candidates if fact.semantic_key in keys]
                if narrowed:
                    candidates = narrowed
                break
        # Evidence IDs plus kind/unit are the narrowing key.  This is never a
        # closest-number operation: two candidate facts means no repair.
        # Backend display compatibility can duplicate the same authoritative
        # value under a projection key; identical replacements are not an
        # ambiguity. Distinct values remain unrepairable.
        unique = {(fact.value, fact.kind, fact.unit): fact for fact in candidates}
        if len(unique) != 1:
            return text, 0
        fact = next(iter(unique.values()))
        return text[:mention.start] + render_fact(fact) + text[mention.end:], 1

    @staticmethod
    def _remove_invalid_numeric_sentences(text: str, payloads: list[dict]) -> str:
        kept: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            if not sentence:
                continue
            try:
                DecisionNarrativeProvider._validate_numbers(sentence, payloads)
            except ValueError as exc:
                if not str(exc).startswith("unsupported_numeric_claim"):
                    raise
                continue
            kept.append(sentence)
        return " ".join(kept)

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
    def _validate_ingredient_semantics(text: str, items: list[dict]):
        normalized = _semantic_normalize(text).replace("đ", "d")
        ingredient_scope = any(item.get("type") in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY", "DEMAND_ORDER_ALIGNMENT", "NO_PLANNED_PURCHASE_BASELINE", "INGREDIENT_OPERATIONAL_RISK"} for item in items)
        if ingredient_scope and not is_causal_limitation(text) and any(word in normalized for word in ("thieu hut", "du kien thieu", "shortage")):
            shortage_items = [item for item in items if item.get("type") in {"NO_PLANNED_PURCHASE_BASELINE", "INGREDIENT_OPERATIONAL_RISK"}]
            if not shortage_items:
                raise ValueError("unsupported_shortage_concept")
            mentioned_shortage = re.search(r"(?:thieu hut|du kien thieu|shortage)(?:\s+(?:la|of|khoang|about))?\s+([0-9][\d.,]*)", normalized)
            if mentioned_shortage:
                mention = next(iter_numeric_mentions(mentioned_shortage.group(1)), None)
                shortage_facts = [fact for fact in build_numeric_authority(shortage_items) if fact.semantic_key == "shortage_quantity"]
                if mention is None or not any(
                    equivalent_or_rounded(mention, fact, approximate=False)
                    or any(
                        (parsed := parse_numeric_mention(display)) is not None and parsed[0] == mention.value
                        for display in fact.display_mentions
                    )
                    for fact in shortage_facts
                ):
                    raise ValueError("unsupported_shortage_quantity")
            if any(phrase in normalized for phrase in ("ke hoach hien tai thieu", "selected plan shortage", "current plan shortage")):
                if not any(item.get("type") == "INGREDIENT_OPERATIONAL_RISK" for item in shortage_items):
                    raise ValueError("baseline_misrepresented_as_selected_plan")
        alignment = next((item for item in items if item.get("type") == "DEMAND_ORDER_ALIGNMENT"), None)
        if alignment and any(word in normalized for word in ("cao hon", "thap hon", "above", "below")):
            gap = alignment.get("absolute_gap")
            if isinstance(gap, (int, float)):
                if (gap < 0 and any(word in normalized for word in ("cao hon", "above"))) or (gap > 0 and any(word in normalized for word in ("thap hon", "below"))):
                    raise ValueError("alignment_direction_contradicted")

    @staticmethod
    def _validate_public_text(text: str):
        """Machine codes are evidence identifiers, never manager-facing prose."""
        if re.fullmatch(r"[!`~\s]{16,}", text):
            raise ValueError("degenerate_provider_answer")
        if re.search(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", text):
            raise ValueError("raw_machine_code_in_narrative")
        if re.search(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", text, re.IGNORECASE):
            raise ValueError("uuid_in_narrative")

    @staticmethod
    def _validate_supported_concepts(text: str, items: list[dict]):
        lowered = text.lower()
        required = {
            ("tồn an toàn", "safety stock"): lambda: any(item.get("code") == "SAFETY_STOCK_PROTECTION" for item in items),
            ("moq", "đặt tối thiểu"): lambda: any(item["type"] == "MOQ" or item.get("code") == "MOQ_CONSTRAINT" for item in items),
            ("hạn dùng", "hết hạn"): lambda: any(item["type"] == "EXPIRY" or item.get("code") == "EXPIRING_INVENTORY" for item in items),
            ("lead time", "thời gian giao"): lambda: any(item["type"] == "LEAD_TIME" or item.get("code") == "LEAD_TIME_PRESSURE" for item in items),
            ("ngân sách", "budget"): lambda: any(item["type"] in {"BUDGET", "BUDGET_STATUS"} or item.get("code") == "BUDGET_CONSTRAINT" for item in items),
            ("rủi ro",): lambda: any(
                item["type"] == "RISK"
                or item["type"] in {"SELECTED_PLAN_RISK_METRICS", "INGREDIENT_OPERATIONAL_RISK", "STRESS_SHORTAGE_OBSERVED", "STRESS_CAPACITY_VIOLATION"}
                or item.get("code") == "STOCKOUT_RISK"
                or (
                    item["type"] in {"STRATEGY_CANDIDATE_METRICS", "STRATEGY_COMPARISON"}
                    and (item.get("stockout_probability") is not None or item.get("stockout_probability_delta") is not None)
                )
                for item in items
            ),
            ("xác suất thiếu", "stockout probability"): lambda: any(
                item.get("stockout_probability") is not None or item.get("stockout_probability_delta") is not None
                for item in items
            ),
        }
        for phrases, is_supported in required.items():
            if any(phrase in lowered for phrase in phrases) and not is_supported():
                raise ValueError("unsupported_causal_concept")

    @staticmethod
    def _validate_risk_provenance(text: str, items: list[dict]):
        """A stress projection cannot be narrated as selected-plan or actual stockout."""
        risk_types = {"SELECTED_PLAN_RISK_METRICS", "RISK", "INGREDIENT_OPERATIONAL_RISK", "STRESS_SHORTAGE_OBSERVED", "STRESS_CAPACITY_VIOLATION"}
        if not any(item.get("type") in risk_types for item in items):
            return
        normalized = _semantic_normalize(text).replace("đ", "d")
        if any(phrase in normalized for phrase in ("da xay ra", "da het hang", "actually ran out", "actual stockout")):
            raise ValueError("projected_risk_misrepresented_as_actual")
        selected_shortage = any(
            (item.get("type") in {"SELECTED_PLAN_RISK_METRICS", "RISK"}
             or item.get("type") == "INGREDIENT_OPERATIONAL_RISK" and item.get("basis_kind") != "conservative_design_scenario")
            and isinstance(item.get("shortage_quantity"), (int, float)) and item["shortage_quantity"] > 0
            for item in items
        )
        selected_shortage_claim = any(phrase in normalized for phrase in (
            "ke hoach hien tai thieu", "ke hoach duoc chon thieu", "ke hoach nay se thieu",
            "selected plan shortage", "current plan shortage",
        )) or bool(re.search(r"ke hoach (?:duoc chon|hien tai|nay) (?:co nguy co|se|dang) (?:thieu|het hang)", normalized))
        if not selected_shortage and selected_shortage_claim:
            raise ValueError("stress_risk_misrepresented_as_selected_plan")

    @staticmethod
    def _validate_risk_shortage_concept(text: str, items: list[dict]):
        """Only the risk-chat scope needs this stricter shortage-versus-capacity check."""
        lowered = text.casefold()
        if not any(marker in lowered for marker in ("nguy cơ thiếu", "thiếu hàng", "shortage")):
            return
        if any(
            item.get("type") == "STRESS_SHORTAGE_OBSERVED"
            or item.get("type") == "INGREDIENT_OPERATIONAL_RISK" and (
                (item.get("shortage_quantity") or 0) > 0 or (item.get("stockout_event_count") or 0) > 0
            )
            or item.get("type") in {"SELECTED_PLAN_RISK_METRICS", "RISK"} and (item.get("shortage_quantity") or 0) > 0
            for item in items
        ):
            return
        raise ValueError("unsupported_risk_shortage_concept")

    @staticmethod
    def _validate_causal_language(text: str, items: list[dict]):
        if not asserts_cause(text):
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


    @staticmethod
    def _validate_strategy_semantics(text: str, items: list[dict]):
        """Accept only relations already materialized by semantic evidence."""
        normalized = _semantic_normalize(text)
        comparisons = [item for item in items if item.get("type") == "STRATEGY_COMPARISON"]
        proofs = [item for item in items if item.get("type") == "STRATEGY_SELECTION_PROOF"]
        ranking_words = ("tot nhat", "an toan nhat", "toi uu nhat", "re nhat", "thap nhat", "cao nhat", "best", "safest", "optimal", "lowest", "highest", "cheapest")
        if any(word in normalized for word in ranking_words):
            if not _selection_proof_authorizes_lowest_cost(normalized, proofs):
                raise ValueError("unsupported_strategy_ranking")
        if any(marker in normalized for marker in ("duoc chon vi", "selected because")):
            if not _selection_proof_authorizes_lowest_cost(normalized, proofs):
                raise ValueError("unsupported_selection_cause")
        if not comparisons:
            return
        for comparison in comparisons:
            if not _comparison_premise_is_stated(comparison, [normalized]):
                raise ValueError("unsupported_comparative_claim")


def _semantic_normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _strategy_aliases(strategy: object) -> tuple[str, ...]:
    normalized = _semantic_normalize(str(strategy))
    return {
        "balanced": ("balanced", "balance", "can bang"),
        "protected": ("protected", "protect", "an toan"),
        "lean": ("lean", "tiet kiem"),
    }.get(normalized, (normalized,))


def _comparison_premise_is_stated(comparison: dict, clauses: list[str]) -> bool:
    left = _strategy_aliases(comparison.get("left_strategy"))
    right = _strategy_aliases(comparison.get("right_strategy"))
    metrics = (
        ("purchase_cost_delta", ("chi phi", "purchase cost", "cost")),
        ("expected_fill_rate_delta", ("fill rate", "muc dap ung")),
        ("stockout_probability_delta", ("xac suat thieu", "stockout probability", "rui ro thieu")),
    )
    for delta_key, metric_words in metrics:
        delta = comparison.get(delta_key)
        if isinstance(delta, bool):
            continue
        try:
            directional_delta = float(delta)
        except (TypeError, ValueError):
            continue
        if directional_delta == 0:
            continue
        expected = ("thap hon", "lower", "less") if directional_delta < 0 else ("cao hon", "higher", "more")
        for clause in clauses:
            left_positions = [clause.find(alias) for alias in left if clause.find(alias) >= 0]
            right_positions = [clause.find(alias) for alias in right if clause.find(alias) >= 0]
            metric_positions = [clause.find(word) for word in metric_words if clause.find(word) >= 0]
            direction_positions = [clause.find(word) for word in expected if clause.find(word) >= 0]
            if (left_positions and right_positions and metric_positions and direction_positions
                    and ("than" in clause or "hon" in clause)
                    and min(left_positions) < min(metric_positions + direction_positions) < max(metric_positions + direction_positions) < min(right_positions)):
                return True
    return False


def _selection_proof_authorizes_lowest_cost(text: str, proofs: list[dict]) -> bool:
    if not any(proof.get("rule") == "lowest_valid_candidate_cost_then_strategy_name"
               and proof.get("selection_metric") == "purchase_cost" for proof in proofs):
        return False
    has_cost = any(word in text for word in ("chi phi", "purchase cost", "cost", "re nhat", "lowest"))
    has_eligibility = any(word in text for word in ("du dieu kien", "eligible", "kha thi", "valid candidate"))
    return has_cost and has_eligibility


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
