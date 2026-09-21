"""Focused application orchestration for one persisted Decision explanation."""

import logging

from app.core.exceptions import PlanningError
from app.services.decision.decision_run_ingredient_scope import decision_run_ingredient_ids
from app.services.decision.explanation_ingredient_resolver import ExplanationIngredientResolver
from app.services.decision.explanation_query_interpretation import (
    ExplanationQueryIntent,
    classify_explanation_question,
)


logger = logging.getLogger("shelfcash.planning")


class ExplainDecision:
    """Explain authorized Decision facts without recomputing or persisting them."""

    def __init__(self, build_decision_brief, read_decision_package, settings, llm_provider, session_factory=None):
        self._build_decision_brief = build_decision_brief
        self._read_decision_package = read_decision_package
        self._session_factory = session_factory or getattr(build_decision_brief, "_session_factory", None)
        self._settings = settings
        self._llm_provider = llm_provider

    def explain(self, decision_run_id: str, body):
        from app.decision_intelligence.narrative import DecisionNarrativeProvider
        from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder

        package = self._read_decision_package.read(decision_run_id)
        requested_ingredient_id = body.ingredient_id
        query_intent = classify_explanation_question(
            body.question, has_explicit_ingredient_id=bool(requested_ingredient_id),
        )
        if query_intent is ExplanationQueryIntent.UNSUPPORTED:
            raise PlanningError(
                "EXPLANATION_QUERY_UNSUPPORTED",
                "Unable to determine a supported explanation request.",
                {"query_classification": "unsupported"},
                http_status=422,
            )
        if requested_ingredient_id:
            # Validate against the immutable Decision Run snapshot before any LLM call.
            # An ID in another store/current inventory must not be substituted here.
            self._require_decision_ingredient(package, decision_run_id, requested_ingredient_id)
            brief = self._build_decision_brief.build(decision_run_id)
            resolution = self._resolve_question(body.question, brief.store_id, package)
            if (
                resolution.status == "resolved"
                and resolution.ingredient_id != requested_ingredient_id
                and resolution.mismatch_safe
            ):
                raise PlanningError(
                    "INGREDIENT_QUESTION_MISMATCH",
                    "Ingredient in question does not match ingredient_id.",
                    {"ingredient_id": requested_ingredient_id, "question_ingredient_id": resolution.ingredient_id, "resolution_source": resolution.source},
                    http_status=422,
                )
            return self._explain_ingredient(
                decision_run_id, body, package, requested_ingredient_id, brief=brief,
            )
        try:
            brief = self._build_decision_brief.build(decision_run_id)
            if query_intent is ExplanationQueryIntent.GENERIC:
                semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
                return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
                    brief, question=body.question, language=body.language,
                    detail_level=body.detail_level, semantic_facts=semantic_facts,
                ).model_dump(mode="json")
            resolution = self._resolve_question(body.question, brief.store_id, package)
            if resolution.status == "ambiguous":
                raise PlanningError(
                    "INGREDIENT_RESOLUTION_AMBIGUOUS",
                    "Unable to identify a unique ingredient from the question.",
                    {"mention": resolution.mention, "candidates": [{"ingredient_id": ingredient_id, "ingredient_name": name} for ingredient_id, name in resolution.candidates]},
                    http_status=422,
                )
            if resolution.status == "not_found" and resolution.mention:
                raise PlanningError(
                    "INGREDIENT_RESOLUTION_NOT_FOUND",
                    "Unable to identify an ingredient from the question.",
                    {"mention": resolution.mention, "resolution_scope": "decision_run"},
                    http_status=422,
                )
            if resolution.ingredient_id:
                return self._explain_ingredient(
                    decision_run_id, body, package, resolution.ingredient_id, brief=brief,
                )
            semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
            return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
                brief, question=body.question, language=body.language,
                detail_level=body.detail_level, semantic_facts=semantic_facts,
            ).model_dump(mode="json")
        except PlanningError:
            raise
        except Exception:
            logger.exception("decision_intelligence_failed decision_run_id=%s", decision_run_id)
            return self._template_explanation(decision_run_id, body)

    def _resolve_question(self, question, store_id: str, package: dict):
        if self._session_factory is None:
            from app.services.decision.explanation_ingredient_resolver import IngredientResolution
            return IngredientResolution(status="not_found")
        with self._session_factory() as session:
            return ExplanationIngredientResolver(session).resolve(
                question=question,
                store_id=store_id,
                decision_run_ingredient_ids=decision_run_ingredient_ids(package),
            )

    def _explain_ingredient(self, decision_run_id: str, body, package: dict, ingredient_id: str, *, brief=None):
        from app.decision_intelligence.narrative import DecisionNarrativeProvider
        from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder

        self._require_decision_ingredient(package, decision_run_id, ingredient_id)
        brief = brief or self._build_decision_brief.build(decision_run_id)
        semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
        return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
            brief, question=body.question, language=body.language,
            detail_level=body.detail_level, semantic_facts=semantic_facts,
            ingredient_id=ingredient_id,
        ).model_dump(mode="json")

    @staticmethod
    def _require_decision_ingredient(package: dict, decision_run_id: str, ingredient_id: str):
        if ingredient_id not in decision_run_ingredient_ids(package):
            raise PlanningError(
                "DECISION_RUN_INGREDIENT_NOT_FOUND",
                "Ingredient is not present in this Decision Run.",
                {"decision_run_id": decision_run_id, "ingredient_id": ingredient_id},
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
