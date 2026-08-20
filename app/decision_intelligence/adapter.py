from __future__ import annotations

import json

from shelfcash_forecast.decision_intelligence.contracts import DecisionGraph, EvidencePackage
from shelfcash_forecast.decision_intelligence.evidence import EvidenceCollector
from shelfcash_forecast.decision_intelligence.retrieval import StructuredLocalRetriever, build_retrieval_context

from app.decision_intelligence.contracts import (
    Citation, DecisionBriefFacts, DecisionExplanationResponse, EvidenceBrief, ExplanationClaim,
)
from app.decision_intelligence.semantic_evidence import SemanticFact


def ingredient_scoped_semantic_facts(
    facts: list[SemanticFact], ingredient_id: str,
) -> list[SemanticFact]:
    """Return target facts plus the one safe run-level context fact.

    The target ID comes from the request/service boundary, not from model
    inference.  Do not let a run-level metric masquerade as an ingredient fact.
    """
    return [
        fact for fact in facts
        if fact.entities.get("ingredient_id") == ingredient_id
        or fact.fact_type == "PLAN_OVERVIEW"
    ]


class ShelfCashDecisionIntelligenceAdapter:
    """Translate immutable backend facts to M6 evidence/retrieval, read-only."""

    provider = "shelfcash_decision_intelligence"

    def explain(
        self, brief: DecisionBriefFacts, *, question: str | None, language: str,
        detail_level: str, semantic_facts: list[SemanticFact] | None = None,
    ) -> DecisionExplanationResponse:
        question = question or ("Why is this plan recommended?" if language == "en" else "Tại sao kế hoạch này được đề xuất?")
        evidence = self._evidence(brief, semantic_facts=semantic_facts)
        graph = DecisionGraph(request_id=brief.decision_run_id, nodes=[], edges=[])
        retrieved = StructuredLocalRetriever().retrieve(question, evidence, graph, context=build_retrieval_context(question, evidence, recommended_strategy=(brief.recommendation.strategy or "").upper() or None))
        if not brief.recommendation.available:
            relevant = [item for item in evidence.items if item.evidence_type in {"recommendation", "critic_verdict"}]
            return self._response(brief, language, detail_level, "NO_FEASIBLE_PLAN", relevant, no_feasible=True)
        if not retrieved.items:
            text = "Insufficient evidence to confirm this request." if language == "en" else "Không đủ dữ liệu để xác nhận."
            return DecisionExplanationResponse(source="template", language=language, detail_level=detail_level, summary=text, why_this_plan=[text], main_risks=[], tradeoffs=[], important_assumptions=[], decision_run_id=brief.decision_run_id, answer=text, intent=retrieved.intent, entities={"ingredient_ids": [], "supplier_ids": []}, claims=[], citations=[], grounded=True, provider=self.provider)
        return self._response(brief, language, detail_level, retrieved.intent, retrieved.items)

    def evidence_briefs(
        self, brief: DecisionBriefFacts, semantic_facts: list[SemanticFact] | None = None,
    ) -> list[EvidenceBrief]:
        return [EvidenceBrief(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object, entities=item.entities) for item in self._evidence(brief, semantic_facts=semantic_facts).items]

    def ingredient_evidence(
        self,
        brief: DecisionBriefFacts,
        *,
        ingredient_id: str,
        semantic_facts: list[SemanticFact],
    ):
        """Build a bounded, entity-isolated evidence package for one ingredient."""
        scoped_facts = ingredient_scoped_semantic_facts(semantic_facts, ingredient_id)
        package = self._evidence(brief, semantic_facts=scoped_facts)
        selected = []
        for item in package.items:
            item_ingredient_id = item.entities.get("ingredient_id")
            if item_ingredient_id == ingredient_id:
                selected.append(item)
                continue
            # PLAN_OVERVIEW is the only allowed run context.  It supports
            # horizon/strategy/status wording but never target quantities.
            if (
                item.evidence_type == "semantic_plan_overview"
                and not item_ingredient_id
            ):
                selected.append(item)
        return EvidencePackage(
            request_id=package.request_id,
            items=sorted(selected, key=lambda item: item.evidence_id),
            source_layers=sorted({item.layer for item in selected}),
            provenance={
                **package.provenance,
                "scope": "ingredient",
                "target_ingredient_id": ingredient_id,
            },
        )

    def explain_ingredient(
        self,
        brief: DecisionBriefFacts,
        *,
        ingredient_id: str,
        language: str,
        detail_level: str,
        semantic_facts: list[SemanticFact],
    ) -> DecisionExplanationResponse:
        """Human-readable deterministic fallback for an explicitly targeted ingredient."""
        scoped_facts = ingredient_scoped_semantic_facts(semantic_facts, ingredient_id)
        evidence = self.ingredient_evidence(
            brief, ingredient_id=ingredient_id, semantic_facts=scoped_facts,
        )
        semantic_item_by_id = {
            str(item.payload.get("semantic_fact_id")): item
            for item in evidence.items
            if item.evidence_type.startswith("semantic_")
        }
        by_type = {fact.fact_type: fact for fact in scoped_facts if fact.entities.get("ingredient_id") == ingredient_id}
        display_name = next(
            (row.ingredient_name for row in brief.ingredient_demand if row.ingredient_id == ingredient_id and row.ingredient_name),
            None,
        ) or next(
            (row.ingredient_name for row in brief.procurement_rows if row.ingredient_id == ingredient_id and row.ingredient_name),
            None,
        ) or ingredient_id

        lines: list[str] = []
        claims: list[ExplanationClaim] = []
        citations: list[Citation] = []

        def add_fact_line(fact_type: str, text: str, value: object = None, unit: str | None = None) -> None:
            fact = by_type.get(fact_type)
            item = semantic_item_by_id.get(fact.fact_id) if fact else None
            if not item:
                return
            lines.append(text)
            claims.append(ExplanationClaim(type=fact_type, value=value if value is not None else text, unit=unit, evidence_ids=[item.evidence_id]))
            citations.append(Citation(evidence_id=item.evidence_id, label=item.text, source_type=item.source_object))

        if not brief.recommendation.available:
            text = (
                f"No feasible purchase quantity is recorded for {display_name}."
                if language == "en"
                else f"ShelfCash ch\u01b0a c\u00f3 l\u01b0\u1ee3ng nh\u1eadp kh\u1ea3 thi cho {display_name} trong k\u1ebf ho\u1ea1ch hi\u1ec7n t\u1ea1i."
            )
            lines.append(text)
        else:
            demand = by_type.get("DEMAND_HORIZON_SUMMARY")
            if demand:
                unit = str(demand.values.get("unit") or "")
                total = _display_number(demand.values.get("p50_total"))
                horizon = brief.forecast.horizon_days
                text = (
                    f"Median demand for {display_name} over the next {horizon} days is about {total} {unit}."
                    if language == "en"
                    else f"Nhu c\u1ea7u trung v\u1ecb c\u1ee7a {display_name} trong {horizon} ng\u00e0y t\u1edbi kho\u1ea3ng {total} {unit}."
                )
                add_fact_line("DEMAND_HORIZON_SUMMARY", text, demand.values.get("p50_total"), unit)
            order = by_type.get("PROCUREMENT_QUANTITY")
            if order:
                unit = str(order.values.get("unit") or "")
                quantity = _display_number(order.values.get("quantity"))
                text = (
                    f"The current plan proposes ordering {quantity} {unit} of {display_name}."
                    if language == "en"
                    else f"K\u1ebf ho\u1ea1ch hi\u1ec7n \u0111\u1ec1 xu\u1ea5t nh\u1eadp {quantity} {unit} {display_name}."
                )
                add_fact_line("PROCUREMENT_QUANTITY", text, order.values.get("quantity"), unit)
            elif demand:
                lines.append(
                    f"No purchase quantity is currently proposed for {display_name}."
                    if language == "en"
                    else f"Hi\u1ec7n ch\u01b0a c\u00f3 l\u01b0\u1ee3ng mua \u0111\u01b0\u1ee3c \u0111\u1ec1 xu\u1ea5t cho {display_name}."
                )
            alignment = by_type.get("DEMAND_ORDER_ALIGNMENT")
            if alignment:
                unit = str(alignment.values.get("unit") or "")
                quantity = _display_number(alignment.values.get("order_quantity_total"))
                gap = _display_number(alignment.values.get("absolute_gap_magnitude"))
                text = (
                    f"The planned quantity is {gap} {unit} above the median-demand total."
                    if language == "en"
                    else f"L\u01b0\u1ee3ng \u0111\u1eb7t cao h\u01a1n t\u1ed5ng nhu c\u1ea7u trung v\u1ecb {gap} {unit}."
                )
                add_fact_line("DEMAND_ORDER_ALIGNMENT", text, alignment.values.get("absolute_gap_magnitude"), unit)
            baseline = by_type.get("NO_PLANNED_PURCHASE_BASELINE")
            if baseline and baseline.values.get("shortage_quantity") is not None:
                unit = str(baseline.values.get("unit") or "")
                shortage = _display_number(baseline.values.get("shortage_quantity"))
                text = (
                    f"With planned purchases from this decision excluded, the simulation shows a shortage of {shortage} {unit}."
                    if language == "en"
                    else f"Trong m\u00f4 ph\u1ecfng kh\u00f4ng b\u1ed5 sung l\u01b0\u1ee3ng mua m\u1edbi t\u1eeb k\u1ebf ho\u1ea1ch n\u00e0y, {display_name} d\u1ef1 ki\u1ebfn thi\u1ebfu {shortage} {unit}."
                )
                add_fact_line("NO_PLANNED_PURCHASE_BASELINE", text, baseline.values.get("shortage_quantity"), unit)

        answer = " ".join(lines) or (
            "Insufficient evidence is available for this ingredient."
            if language == "en" else "Ch\u01b0a \u0111\u1ee7 d\u1eef li\u1ec7u cho nguy\u00ean li\u1ec7u n\u00e0y."
        )
        return DecisionExplanationResponse(
            source="template", language=language, detail_level=detail_level,
            summary=answer, why_this_plan=lines, main_risks=[], tradeoffs=[],
            important_assumptions=["Explanation is grounded in the persisted decision package."],
            decision_run_id=brief.decision_run_id, answer=answer,
            intent="EXPLAIN_INGREDIENT_PROCUREMENT",
            entities={"ingredient_ids": [ingredient_id], "supplier_ids": []},
            claims=claims, citations=citations, grounded=True, provider=self.provider,
        )

    def _evidence(self, brief: DecisionBriefFacts, semantic_facts: list[SemanticFact] | None = None):
        collector = EvidenceCollector(brief.decision_run_id)
        collector.add(layer="M5", evidence_type="recommendation", source_object="DecisionRun.package_json", source_path="package.recommended_strategy", semantics="critic_verdict", entities={"store_id": brief.store_id, "strategy": (brief.recommendation.strategy or "UNAVAILABLE").upper()}, payload={"recommended_strategy": brief.recommendation.strategy, "status": brief.status, "available": brief.recommendation.available}, text=f"Persisted recommendation: {brief.recommendation.strategy or 'no feasible recommendation'}.")
        collector.add(layer="M5", evidence_type="critic_verdict", source_object="DecisionRun.package_json", source_path="package.critic", semantics="critic_verdict", entities={"store_id": brief.store_id, "strategy": (brief.recommendation.strategy or "UNAVAILABLE").upper()}, payload={"hard_violations": brief.critic.hard_violations, "warnings": brief.critic.warnings}, text=f"Persisted critic findings: {', '.join(brief.critic.hard_violations) or 'none'}.")
        for index, row in enumerate(brief.procurement_rows):
            collector.add(layer="M5", evidence_type="first_stage_order", source_object="DecisionRun.package_json", source_path=f"package.recommended_plan.items[{index}]", semantics="solver_estimate", entities={"store_id": brief.store_id, "ingredient_id": row.ingredient_id, **({"supplier_id": row.supplier_id} if row.supplier_id else {})}, payload={"quantity": row.quantity, "unit": row.unit, "supplier_id": row.supplier_id, "purchase_cost": row.purchase_cost, "reason_codes": row.reason_codes}, text=f"Persisted order for {row.ingredient_name or row.ingredient_id}: {row.quantity} {row.unit or ''}.")
        for index, demand in enumerate(brief.ingredient_demand):
            target_date = demand.target_date.isoformat() if hasattr(demand.target_date, "isoformat") else str(demand.target_date)
            collector.add(layer="M3", evidence_type="ingredient_demand", source_object="DecisionRun.package_json", source_path=f"package.ingredient_demand[ingredient_id={demand.ingredient_id};target_date={target_date}]", semantics="quantile", entities={"store_id": brief.store_id, "ingredient_id": demand.ingredient_id, "target_date": target_date}, payload={"target_date": target_date, "p25": demand.p25, "p50": demand.p50, "p75": demand.p75, "unit": demand.unit}, text=f"Persisted ingredient demand for {demand.ingredient_name or demand.ingredient_id} on {target_date}: P50={demand.p50} {demand.unit or ''}.")
        risk_payload = {
            "stockout_probability": brief.risk.stockout_probability,
            "expected_fill_rate": brief.risk.expected_fill_rate,
            "shortage_quantity": brief.risk.shortage_quantity,
            "waste_quantity": brief.risk.waste_quantity,
        }
        if any(value is not None for value in risk_payload.values()):
            collector.add(layer="M5", evidence_type="inventory_risk", source_object="DecisionRun.package_json", source_path="package.inventory_risk / package.business_metrics", semantics="deterministic", entities={"store_id": brief.store_id}, payload=risk_payload, text="Persisted inventory risk metrics for the recommended decision.")
        for fact in semantic_facts or []:
            payload = {
                "semantic_fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "classification": fact.classification.value,
                **fact.values,
            }
            collector.add(
                layer="M5",
                evidence_type=f"semantic_{fact.fact_type.lower()}",
                source_object=fact.provenance.source_type,
                source_path=fact.provenance.source_path,
                semantics=(
                    "stress"
                    if fact.classification.value == "RISK_SIGNAL"
                    else "deterministic"
                ),
                entities={"store_id": brief.store_id, **fact.entities},
                payload=payload,
                # Machine-readable retrieval metadata; never returned as an explanation.
                text=f"{fact.fact_type} {json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}",
            )
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


def _display_number(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    rendered = f"{float(value):,.2f}".rstrip("0").rstrip(".")
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")
