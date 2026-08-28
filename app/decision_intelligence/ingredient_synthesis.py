"""Backend-owned ingredient presentation with bounded, batched narration."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    DecisionBriefFacts, IngredientSynthesis, IngredientSynthesisLLMResponse,
)
from app.decision_intelligence.display import add_numeric_display_contract, vi_number
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import SemanticFact
from app.decision_intelligence.style_examples import retrieve_style_examples
from app.llm.tasks import LLMTask

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

        if not eligible or not self.llm_provider or not self.llm_provider.available:
            return [prepared[item][0] for item in ingredient_ids]
        return self._batch_or_fallback(brief, prepared, eligible, evidence_items, ingredient_ids)

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
        if any(item.get("first_stockout_date") for item in operational) or any(item.severity == "critical" for item in risk_details):
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

    def _batch_or_fallback(self, brief, prepared, eligible, evidence_items, ingredient_ids):
        context: dict[str, Any] = {"decision_run_id": brief.decision_run_id}
        logger.info("ingredient_synthesis_batch_started decision_run_id=%s eligible_count=%s task=%s", brief.decision_run_id, len(eligible), LLMTask.INGREDIENT_SYNTHESIS.value)
        try:
            raw = self._run({"task": "ingredient_synthesis", "language": "vi", "ingredients": eligible, "style_examples": retrieve_style_examples(task="ingredient_synthesis", intent="INGREDIENT", case="DEFAULT", detail_level="simple")}, context)
            typed = IngredientSynthesisLLMResponse.model_validate(raw)
        except Exception as exc:
            logger.warning("ingredient_synthesis_batch_fallback decision_run_id=%s eligible_count=%s reason=%s", brief.decision_run_id, len(eligible), type(exc).__name__)
            return [self._fallback(prepared[item][0]) if item in {entry["ingredient_id"] for entry in eligible} else prepared[item][0] for item in ingredient_ids]
        returned = {item.ingredient_id: item for item in typed.items}
        results = []
        eligible_ids = {entry["ingredient_id"] for entry in eligible}
        for ingredient_id in ingredient_ids:
            fallback, records, importance = prepared[ingredient_id]
            if ingredient_id not in eligible_ids:
                results.append(fallback); continue
            item = returned.get(ingredient_id)
            try:
                if item is None:
                    raise ValueError("missing_batch_item")
                allowed = {record["evidence_id"] for record in records}
                if not set(item.used_evidence_ids) <= allowed:
                    raise ValueError("ingredient_evidence_entity_mismatch")
                guarded = self.guard._guard({"answer": f"{item.headline}. {item.summary}", "claims": [claim.model_dump(mode="json") for claim in item.claims], "used_evidence_ids": item.used_evidence_ids}, records, evidence_items, brief, "vi", "simple", "INGREDIENT_SYNTHESIS", target_ingredient_id=ingredient_id)
                results.append(IngredientSynthesis(ingredient_id=ingredient_id, ingredient_name=fallback.ingredient_name, unit=fallback.unit, importance=importance, source="llm", headline=item.headline, summary=item.summary, evidence_ids=[citation.evidence_id for citation in guarded.citations]))
                logger.info("ingredient_synthesis_item decision_run_id=%s ingredient_id=%s validation=passed source=llm", brief.decision_run_id, ingredient_id)
            except Exception as exc:
                logger.warning("ingredient_synthesis_item decision_run_id=%s ingredient_id=%s validation=failed fallback_reason=%s", brief.decision_run_id, ingredient_id, type(exc).__name__)
                results.append(self._fallback(fallback))
        return results

    @staticmethod
    def _fallback(item):
        return item.model_copy(update={"source": "deterministic_fallback"})

    def _run(self, payload, context):
        coroutine = self.llm_provider.generate_json(SYSTEM_PROMPT, payload, task=LLMTask.INGREDIENT_SYNTHESIS, request_context=context)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coroutine)).result()
