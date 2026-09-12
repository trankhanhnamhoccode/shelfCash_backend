"""Focused application orchestration for one current Decision Brief."""

import json
import logging

from app.core.exceptions import PlanningError
from app.core.logging_context import get_request_id
from app.models.decision import DecisionRunModel


logger = logging.getLogger("shelfcash.planning")


class BuildDecisionBrief:
    """Build a read-only Brief from persisted state and existing collaborators."""

    def __init__(self, session_factory, settings, llm_provider):
        self._session_factory = session_factory
        self._settings = settings
        self._llm_provider = llm_provider

    def build(self, decision_run_id: str):
        from app.decision_intelligence import DecisionBriefBuilder, ShelfCashDecisionIntelligenceAdapter
        from app.decision_intelligence.ingredient_synthesis import IngredientSynthesisProvider
        from app.decision_intelligence.overall_summary import OverallSummaryProvider
        from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder

        with self._session_factory() as session:
            run = session.get(DecisionRunModel, decision_run_id)
            if not run:
                raise PlanningError(
                    "DECISION_RUN_NOT_FOUND",
                    "Decision run not found.",
                    {"decision_run_id": decision_run_id},
                    http_status=404,
                )
            brief = DecisionBriefBuilder().build(session, run)
            strategy_expression_mode = self._settings.strategy_expression_mode
            if strategy_expression_mode == "llm_polish":
                # Optional expression is read-only: it may replace only presentation
                # text in this response and never writes the Decision Package.
                from app.decision_intelligence.strategy_expression import StrategyExpressionProvider

                expression_result = StrategyExpressionProvider(
                    self._llm_provider, self._settings,
                ).express_result(brief.strategy_evaluations, brief.decision_run_id)
                diagnostics = expression_result.diagnostics
                details = diagnostics.get("details") or {}
                logger.info(
                    "event=strategy_expression.completed decision_run_id=%s request_id=%s "
                    "attempted=%s status=%s source=%s fallback_used=%s skip_reason=%s "
                    "failure_stage=%s provider=%s requested_model=%s resolved_model=%s "
                    "finish_reason=%s strategy_count=%s selected_style_example_ids=%s "
                    "error_message=%s offending_strategy=%s offending_field=%s "
                    "offending_numeric_mentions=%s numeric_failure_kind=%s detected_phrase=%s "
                    "authorized_reason_codes=%s offending_entity=%s authorized_entities=%s",
                    brief.decision_run_id, get_request_id(), diagnostics.get("attempted"),
                    diagnostics.get("status"), diagnostics.get("source"),
                    diagnostics.get("fallback_used"), diagnostics.get("skip_reason"),
                    diagnostics.get("failure_stage"), diagnostics.get("provider"),
                    diagnostics.get("requested_model"), diagnostics.get("resolved_model"),
                    diagnostics.get("finish_reason"), diagnostics.get("strategy_count"),
                    diagnostics.get("selected_style_example_ids"), diagnostics.get("error_message"),
                    details.get("offending_strategy"), details.get("offending_field"),
                    details.get("offending_numeric_mentions"), details.get("numeric_failure_kind"),
                    details.get("detected_phrase"), details.get("authorized_reason_codes"),
                    details.get("offending_entity"), details.get("authorized_entities"),
                )
                brief = brief.model_copy(update={"strategy_evaluations": expression_result.presentations})
            else:
                logger.info(
                    "event=strategy_presentation.completed decision_run_id=%s request_id=%s "
                    "mode=deterministic source=deterministic strategy_count=%s",
                    brief.decision_run_id, get_request_id(), len(brief.strategy_evaluations),
                )
            if brief.assistant_summary is None:
                package = json.loads(run.package_json)
                facts = DecisionSemanticEvidenceBuilder().build(brief, package)
                # Older Decision Runs are read-only: provide safe deterministic text,
                # never spend tokens or backfill package_json during GET.
                brief = brief.model_copy(update={
                    "assistant_summary": OverallSummaryProvider(
                        self._llm_provider, self._settings,
                    ).deterministic_fallback(brief, facts),
                })
            if not brief.ingredient_synthesis:
                package = json.loads(run.package_json)
                facts = DecisionSemanticEvidenceBuilder().build(brief, package)
                # Historical runs remain read-only and do not trigger a new provider call.
                brief = brief.model_copy(update={
                    "ingredient_synthesis": IngredientSynthesisProvider(
                        None, self._settings,
                    ).synthesize(brief, facts),
                })
            # Evidence is derived from the same immutable package, never persisted or used by M1-M5.
            return brief.model_copy(update={
                "evidence": ShelfCashDecisionIntelligenceAdapter().evidence_briefs(brief),
            })
