"""Focused application orchestration for one persisted Decision explanation."""

import logging
import re

from app.core.exceptions import PlanningError
from app.services.decision.decision_run_ingredient_scope import decision_run_ingredient_ids
from app.services.decision.explanation_ingredient_resolver import ExplanationIngredientResolver
from app.services.decision.explanation_query_interpretation import (
    QuestionScope,
    classify_question_scope,
)


logger = logging.getLogger("shelfcash.planning")


class ExplainDecision:
    """Explain authorized Decision facts without recomputing or persisting them."""

    def __init__(self, build_decision_brief, read_decision_package, settings, llm_provider, session_factory=None, what_if_decision=None):
        self._build_decision_brief = build_decision_brief
        self._read_decision_package = read_decision_package
        self._session_factory = session_factory or getattr(build_decision_brief, "_session_factory", None)
        self._settings = settings
        self._llm_provider = llm_provider
        self._what_if_decision = what_if_decision

    def explain(self, decision_run_id: str, body):
        from app.decision_intelligence.narrative import DecisionNarrativeProvider
        from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder

        package = self._read_decision_package.read(decision_run_id)
        if getattr(body, "history", None):
            from app.services.decision.explanation_history import resolve_follow_up
            history_brief = self._build_decision_brief.build(decision_run_id)
            resolution = resolve_follow_up(body.question, body.history, history_brief)
            if resolution.clarification:
                return self._history_clarification(decision_run_id, body, resolution.clarification)
            body = body.model_copy(update={"question": resolution.question})
        requested_ingredient_id = body.ingredient_id
        question_scope = classify_question_scope(
            body.question, has_explicit_ingredient_id=bool(requested_ingredient_id),
        )
        if question_scope is QuestionScope.UNSUPPORTED:
            raise PlanningError(
                "EXPLANATION_QUERY_UNSUPPORTED",
                "Unable to determine a supported explanation request.",
                {"query_classification": "unsupported"},
                http_status=422,
            )
        if question_scope is QuestionScope.BUDGET_WHAT_IF:
            return self._explain_budget_what_if(decision_run_id, body, package)
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
            resolution = None
            if question_scope not in {QuestionScope.BUDGET, QuestionScope.BUDGET_WHAT_IF, QuestionScope.PLAN_STRATEGY, QuestionScope.CLOSED_FACT}:
                resolution = self._resolve_question(body.question, brief.store_id, package)
                if resolution.status == "ambiguous":
                    raise PlanningError(
                        "INGREDIENT_RESOLUTION_AMBIGUOUS",
                        "Multiple Decision Run ingredients match the question.",
                        {"mention": resolution.mention, "resolution_scope": "decision_run",
                         "candidates": [
                             {"ingredient_id": ingredient_id, "ingredient_name": name}
                             for ingredient_id, name in resolution.candidates
                         ]},
                        http_status=422,
                    )
                if resolution.ingredient_id:
                    if resolution.source == "decision_run_id":
                        from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
                        return ShelfCashDecisionIntelligenceAdapter().explain(
                            brief, question=body.question, language=body.language,
                            detail_level=body.detail_level,
                        ).model_dump(mode="json")
                    return self._explain_ingredient(
                        decision_run_id, body, package, resolution.ingredient_id, brief=brief,
                    )
            if question_scope is not QuestionScope.ENTITY_OPERATIONAL:
                # QuestionScope is authoritative for broad retrieval scope.
                # Slice C gives PLAN_STRATEGY a distinct evidence path.
                semantic_facts = DecisionSemanticEvidenceBuilder().build(brief, package)
                return DecisionNarrativeProvider(self._llm_provider, self._settings).explain(
                    brief, question=body.question, language=body.language,
                    detail_level=body.detail_level, semantic_facts=semantic_facts,
                    question_scope=question_scope.value, history=getattr(body, "history", []),
                ).model_dump(mode="json")
            resolution = resolution or self._resolve_question(body.question, brief.store_id, package)
            if resolution.status == "not_found" and resolution.mention:
                raise PlanningError(
                    "INGREDIENT_RESOLUTION_NOT_FOUND",
                    "Unable to identify an ingredient from the question.",
                    {"mention": resolution.mention, "resolution_scope": "decision_run"},
                    http_status=422,
                )
            return self._history_clarification(
                decision_run_id, body, "Bạn muốn hỏi nguyên liệu nào trong Decision Run này?",
            )
        except PlanningError:
            raise
        except Exception:
            logger.exception("decision_intelligence_failed decision_run_id=%s", decision_run_id)
            return self._template_explanation(decision_run_id, body)

    def _explain_budget_what_if(self, decision_run_id: str, body, package: dict):
        from app.schemas.decision import WhatIfRequest
        from app.services.decision.budget_what_if import current_budget_snapshot, resolve_budget_mutation

        mutation = resolve_budget_mutation(body.question, current_budget=current_budget_snapshot(package))
        if mutation.budget_limit is None:
            message = {
                "amount_required": "Bạn muốn đặt hoặc thay đổi ngân sách thành bao nhiêu để tôi so sánh?",
                "current_budget_unavailable": "Decision Run này không có đủ thông tin ngân sách hiện tại để tính mức thay đổi.",
                "invalid_amount": "Mức ngân sách sau thay đổi phải không âm.",
            }.get(mutation.issue, "Không thể xác định mức ngân sách giả định.")
            return self._budget_what_if_message(decision_run_id, body, message)
        if self._what_if_decision is None:
            return self._budget_what_if_message(decision_run_id, body, "Không thể chạy mô phỏng ngân sách ở thời điểm này.")
        result = self._what_if_decision(decision_run_id, WhatIfRequest(budget_limit=mutation.budget_limit))
        explanation = result.get("grounded_explanation") if isinstance(result, dict) else None
        if isinstance(explanation, dict):
            return explanation
        return self._budget_what_if_message(decision_run_id, body, "Không thể tạo phần giải thích cho mô phỏng ngân sách.")

    @staticmethod
    def _budget_what_if_message(decision_run_id: str, body, message: str):
        return {"source": "template", "language": body.language, "detail_level": body.detail_level,
                "summary": message, "why_this_plan": [message], "main_risks": [], "tradeoffs": [],
                "important_assumptions": ["A specific budget is required for a deterministic What-if."],
                "decision_run_id": decision_run_id, "answer": message, "intent": "BUDGET_WHAT_IF",
                "entities": {"ingredient_ids": [], "supplier_ids": []}, "claims": [], "citations": [],
                "grounded": True, "provider": "shelfcash_decision_intelligence"}

    @staticmethod
    def _history_clarification(decision_run_id: str, body, message: str):
        return {"source": "template", "language": body.language, "detail_level": body.detail_level,
                "summary": message, "why_this_plan": [message], "main_risks": [], "tradeoffs": [],
                "important_assumptions": ["Conversation history is context, not business evidence."],
                "decision_run_id": decision_run_id, "answer": message, "intent": "CLARIFICATION",
                "entities": {"ingredient_ids": [], "supplier_ids": []}, "claims": [], "citations": [],
                "grounded": True, "provider": "shelfcash_decision_intelligence"}

    def _resolve_question(self, question, store_id: str, package: dict):
        from app.services.decision.explanation_ingredient_resolver import IngredientResolution
        for ingredient_id in sorted(decision_run_ingredient_ids(package), key=lambda value: (-len(value), value)):
            if re.search(rf"(?<![\w-]){re.escape(ingredient_id)}(?![\w-])", question or "", re.IGNORECASE):
                return IngredientResolution(status="resolved", ingredient_id=ingredient_id, mention=ingredient_id, source="decision_run_id")
        if self._session_factory is None:
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
            history=getattr(body, "history", []),
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
