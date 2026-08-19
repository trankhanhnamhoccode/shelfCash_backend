from __future__ import annotations

from shelfcash_forecast.decision_intelligence.contracts import DecisionGraph
from shelfcash_forecast.decision_intelligence.evidence import EvidenceCollector
from shelfcash_forecast.decision_intelligence.retrieval import StructuredLocalRetriever, build_retrieval_context

from app.decision_intelligence.contracts import (
    Citation, DecisionBriefFacts, DecisionExplanationResponse, EvidenceBrief, ExplanationClaim,
)


class ShelfCashDecisionIntelligenceAdapter:
    """Translate immutable backend facts to M6 evidence/retrieval, read-only."""

    provider = "shelfcash_decision_intelligence"

    def explain(self, brief: DecisionBriefFacts, *, question: str | None, language: str, detail_level: str) -> DecisionExplanationResponse:
        question = question or ("Why is this plan recommended?" if language == "en" else "Tại sao kế hoạch này được đề xuất?")
        evidence = self._evidence(brief)
        graph = DecisionGraph(request_id=brief.decision_run_id, nodes=[], edges=[])
        retrieved = StructuredLocalRetriever().retrieve(question, evidence, graph, context=build_retrieval_context(question, evidence, recommended_strategy=(brief.recommendation.strategy or "").upper() or None))
        if not brief.recommendation.available:
            relevant = [item for item in evidence.items if item.evidence_type in {"recommendation", "critic_verdict"}]
            return self._response(brief, language, detail_level, "NO_FEASIBLE_PLAN", relevant, no_feasible=True)
        if not retrieved.items:
            text = "Insufficient evidence to confirm this request." if language == "en" else "Không đủ dữ liệu để xác nhận."
            return DecisionExplanationResponse(source="template", language=language, detail_level=detail_level, summary=text, why_this_plan=[text], main_risks=[], tradeoffs=[], important_assumptions=[], decision_run_id=brief.decision_run_id, answer=text, intent=retrieved.intent, entities={"ingredient_ids": [], "supplier_ids": []}, claims=[], citations=[], grounded=True, provider=self.provider)
        return self._response(brief, language, detail_level, retrieved.intent, retrieved.items)

    def evidence_briefs(self, brief: DecisionBriefFacts) -> list[EvidenceBrief]:
        return [EvidenceBrief(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object, entities=item.entities) for item in self._evidence(brief).items]

    def _evidence(self, brief: DecisionBriefFacts):
        collector = EvidenceCollector(brief.decision_run_id)
        collector.add(layer="M5", evidence_type="recommendation", source_object="DecisionRun.package_json", source_path="package.recommended_strategy", semantics="critic_verdict", entities={"store_id": brief.store_id, "strategy": (brief.recommendation.strategy or "UNAVAILABLE").upper()}, payload={"recommended_strategy": brief.recommendation.strategy, "status": brief.status, "available": brief.recommendation.available}, text=f"Persisted recommendation: {brief.recommendation.strategy or 'no feasible recommendation'}.")
        collector.add(layer="M5", evidence_type="critic_verdict", source_object="DecisionRun.package_json", source_path="package.critic", semantics="critic_verdict", entities={"store_id": brief.store_id, "strategy": (brief.recommendation.strategy or "UNAVAILABLE").upper()}, payload={"hard_violations": brief.critic.hard_violations, "warnings": brief.critic.warnings}, text=f"Persisted critic findings: {', '.join(brief.critic.hard_violations) or 'none'}.")
        for index, row in enumerate(brief.procurement_rows):
            collector.add(layer="M5", evidence_type="first_stage_order", source_object="DecisionRun.package_json", source_path=f"package.recommended_plan.items[{index}]", semantics="solver_estimate", entities={"store_id": brief.store_id, "ingredient_id": row.ingredient_id, **({"supplier_id": row.supplier_id} if row.supplier_id else {})}, payload={"quantity": row.quantity, "unit": row.unit, "supplier_id": row.supplier_id, "purchase_cost": row.purchase_cost, "reason_codes": row.reason_codes}, text=f"Persisted order for {row.ingredient_name or row.ingredient_id}: {row.quantity} {row.unit or ''}.")
        for index, demand in enumerate(brief.ingredient_demand):
            target_date = demand.target_date.isoformat() if hasattr(demand.target_date, "isoformat") else str(demand.target_date)
            collector.add(layer="M3", evidence_type="ingredient_demand", source_object="DecisionRun.package_json", source_path=f"package.ingredient_demand[ingredient_id={demand.ingredient_id};target_date={target_date}]", semantics="quantile", entities={"store_id": brief.store_id, "ingredient_id": demand.ingredient_id, "target_date": target_date}, payload={"target_date": target_date, "p25": demand.p25, "p50": demand.p50, "p75": demand.p75, "unit": demand.unit}, text=f"Persisted ingredient demand for {demand.ingredient_name or demand.ingredient_id} on {target_date}: P50={demand.p50} {demand.unit or ''}.")
        return collector.package()

    def _response(self, brief, language, detail_level, intent, items, *, no_feasible=False):
        citations = [Citation(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object) for item in items]
        claims = []
        lines = []
        ingredient_ids, supplier_ids = set(), set()
        if no_feasible:
            text = "No feasible recommendation is recorded." if language == "en" else "Không có phương án khả thi được ghi nhận."
            lines.append(text)
        for item in items:
            ingredient = item.entities.get("ingredient_id")
            supplier = item.entities.get("supplier_id")
            if ingredient: ingredient_ids.add(ingredient)
            if supplier: supplier_ids.add(supplier)
            if item.evidence_type == "first_stage_order":
                value = item.payload.get("quantity"); unit = item.payload.get("unit")
                text = f"Order {value} {unit or ''} for {ingredient}." if language == "en" else f"Kế hoạch ghi nhận đặt {value} {unit or ''} cho {ingredient}."
                claims.append(ExplanationClaim(type="ORDER_QUANTITY", value=value, unit=unit, evidence_ids=[item.evidence_id]))
            else:
                text = item.text
                claims.append(ExplanationClaim(type=item.evidence_type.upper(), value=item.payload, evidence_ids=[item.evidence_id]))
            lines.append(text)
        answer = " ".join(lines)
        return DecisionExplanationResponse(source="template", language=language, detail_level=detail_level, summary=answer, why_this_plan=lines, main_risks=brief.critic.warnings, tradeoffs=[], important_assumptions=["Explanation is read-only and grounded in the persisted decision package."], decision_run_id=brief.decision_run_id, answer=answer, intent=intent.upper(), entities={"ingredient_ids": sorted(ingredient_ids), "supplier_ids": sorted(supplier_ids)}, claims=claims, citations=citations, grounded=True, provider=self.provider)
