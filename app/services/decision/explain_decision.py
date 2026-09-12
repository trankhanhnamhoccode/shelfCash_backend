"""Focused application orchestration for one persisted Decision explanation."""

import logging

from app.core.exceptions import PlanningError


logger = logging.getLogger("shelfcash.planning")


class ExplainDecision:
    """Explain authorized Decision facts without recomputing or persisting them."""

    def __init__(self, build_decision_brief, read_decision_package, settings, llm_provider):
        self._build_decision_brief = build_decision_brief
        self._read_decision_package = read_decision_package
        self._settings = settings
        self._llm_provider = llm_provider

    def explain(self, decision_run_id: str, body):
        from app.decision_intelligence.narrative import DecisionNarrativeProvider
        from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder

        if body.ingredient_id:
            # Validate against the immutable Decision Run snapshot before any LLM call.
            # An ID in another store/current inventory must not be substituted here.
            brief = self._build_decision_brief.build(decision_run_id)
            self._require_decision_ingredient(brief, body.ingredient_id)
            package = self._read_decision_package.read(decision_run_id)
            semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
            return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
                brief,
                question=body.question,
                language=body.language,
                detail_level=body.detail_level,
                semantic_facts=semantic_facts,
                ingredient_id=body.ingredient_id,
            ).model_dump(mode="json")
        try:
            brief = self._build_decision_brief.build(decision_run_id)
            package = self._read_decision_package.read(decision_run_id)
            semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
            return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
                brief,
                question=body.question,
                language=body.language,
                detail_level=body.detail_level,
                semantic_facts=semantic_facts,
            ).model_dump(mode="json")
        except Exception:
            logger.exception("decision_intelligence_failed decision_run_id=%s", decision_run_id)
            return self._template_explanation(decision_run_id, body)

    @staticmethod
    def _require_decision_ingredient(brief, ingredient_id: str):
        ingredient_ids = {row.ingredient_id for row in brief.ingredient_demand}
        ingredient_ids.update(row.ingredient_id for row in brief.procurement_rows)
        if ingredient_id not in ingredient_ids:
            raise PlanningError(
                "DECISION_RUN_INGREDIENT_NOT_FOUND",
                "Ingredient is not present in this Decision Run.",
                {"decision_run_id": brief.decision_run_id, "ingredient_id": ingredient_id},
                http_status=422,
            )

    def _template_explanation(self, decision_run_id: str, body):
        """Legacy response retained exclusively as M6 failure fallback."""
        package = self._read_decision_package.read(decision_run_id)
        reasons = package.get("reason_codes", [])
        messages = []
        # Raw package reason codes remain available for compatibility/debugging, but
        # they are not causal proof and must not drive a natural-language fallback.
        mapping = {}
        for reason in reasons:
            if reason.get("code") in mapping:
                messages.append(mapping[reason["code"]])
        if not messages:
            messages.append(
                "\u004b\u1ebf ho\u1ea1ch d\u1ef1a tr\u00ean forecast, BOM, t\u1ed3n kho theo l\u00f4 v\u00e0 c\u00e1c r\u00e0ng bu\u1ed9c nh\u00e0 cung c\u1ea5p hi\u1ec7n c\u00f3."
            )
        summary = " ".join(messages)
        return {
            "source": "template",
            "language": body.language,
            "detail_level": body.detail_level,
            "summary": summary,
            "why_this_plan": messages,
            "main_risks": package.get("warnings", []),
            "tradeoffs": [],
            "important_assumptions": ["Forecast is uncertain and does not guarantee demand."],
            "decision_run_id": decision_run_id,
            "answer": summary,
            "intent": "FALLBACK",
            "entities": {"ingredient_ids": [], "supplier_ids": []},
            "claims": [],
            "citations": [],
            "grounded": False,
            "provider": "legacy_template_fallback",
        }
