"""One-time, grounded overall summaries for persisted Decision Runs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
from app.decision_intelligence.contracts import (
    AssistantSummary,
    DecisionBriefFacts,
    DecisionOverallSummaryLLMResponse,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.decision_intelligence.semantic_evidence import (
    SemanticFact,
    SemanticFactClassification,
    SemanticFactScope,
)
from app.llm.tasks import LLMFailureStage, LLMTask

logger = logging.getLogger("shelfcash.overall_summary")


SYSTEM_PROMPT = """You write a short, grounded Vietnamese overall decision brief for a store manager.

Use only the supplied structured facts. Return Vietnamese text. Do not calculate
new values, infer business causes, compare strategies, mention unsupported
probabilities, expose machine codes, UUIDs, prompts, models, or implementation.

OBSERVATION and DERIVED facts support only factual statements and comparisons.
They never support words such as because, due to, therefore, or causal claims.
Only CAUSAL facts may support causal language. No CAUSAL facts may be assumed.

Use supplied display values exactly when present. Keep the summary to 2-4 short
sentences, a concise headline, and at most three key points. A limitation may be
summarized generically without its machine code. Do not write markdown.

Return exactly one JSON object matching the supplied schema. Each displayed text
field must be repeated as a claim with valid evidence IDs. used_evidence_ids must
be exactly the union of all claim evidence IDs.
"""


def _vi_number(value: float, maximum_decimals: int = 2) -> str:
    rendered = f"{value:,.{maximum_decimals}f}"
    rendered = rendered.rstrip("0").rstrip(".")
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def _strategy_label(strategy: str | None) -> str | None:
    return {
        "protected": "An to\u00e0n",
        "balanced": "C\u00e2n b\u1eb1ng",
        "lean": "Tinh g\u1ecdn",
    }.get(strategy or "")


class OverallSummaryProvider:
    """Uses the existing gateway and narrative guard; never owns business math."""

    def __init__(self, llm_provider, settings):
        self.llm_provider = llm_provider
        self.settings = settings
        self._adapter = ShelfCashDecisionIntelligenceAdapter()
        self._guard = DecisionNarrativeProvider(None, settings)

    def deterministic_fallback(
        self,
        brief: DecisionBriefFacts,
        facts: list[SemanticFact],
    ) -> AssistantSummary:
        overview = next((fact for fact in facts if fact.fact_type == "PLAN_OVERVIEW"), None)
        limitations = [
            fact for fact in facts
            if fact.classification is SemanticFactClassification.LIMITATION
        ]
        stress = [
            fact for fact in facts
            if fact.classification is SemanticFactClassification.RISK_SIGNAL
        ]
        if not brief.recommendation.available:
            return AssistantSummary(
                headline="Ch\u01b0a c\u00f3 k\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng kh\u1ea3 thi",
                summary=(
                    "ShelfCash ch\u01b0a t\u00ecm \u0111\u01b0\u1ee3c k\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng kh\u1ea3 thi "
                    "trong c\u00e1c \u0111i\u1ec1u ki\u1ec7n hi\u1ec7n t\u1ea1i."
                ),
                key_points=[],
                warning_summary=(
                    "M\u1ed9t s\u1ed1 \u0111i\u1ec1u ki\u1ec7n ho\u1eb7c ch\u1ec9 s\u1ed1 c\u1ea7n \u0111\u01b0\u1ee3c xem x\u00e9t th\u00eam."
                    if limitations else None
                ),
                source="deterministic_fallback",
                grounded=True,
            )

        count = int(overview.values.get("ordered_ingredient_count") or 0) if overview else len(brief.procurement_rows)
        horizon = int(overview.values.get("horizon_days") or 0) if overview else brief.forecast.horizon_days
        summary = f"ShelfCash \u0111\u1ec1 xu\u1ea5t nh\u1eadp {count} nguy\u00ean li\u1ec7u"
        cost = overview.values.get("total_purchase_cost") if overview else None
        if isinstance(cost, (int, float)):
            summary += f" v\u1edbi t\u1ed5ng chi ph\u00ed d\u1ef1 ki\u1ebfn {_vi_number(float(cost), 0)} \u0111\u1ed3ng"
        if horizon:
            summary += f" cho {horizon} ng\u00e0y t\u1edbi"
        summary += "."
        strategy = _strategy_label(brief.recommendation.strategy)
        points = [
            f"K\u1ebf ho\u1ea1ch hi\u1ec7n t\u1ea1i s\u1eed d\u1ee5ng chi\u1ebfn l\u01b0\u1ee3c {strategy}."
        ] if strategy else []
        if stress:
            points.append(
                "M\u1ed9t s\u1ed1 k\u1ecbch b\u1ea3n ki\u1ec3m tra ghi nh\u1eadn t\u00edn hi\u1ec7u thi\u1ebfu h\u00e0ng "
                "ho\u1eb7c v\u01b0\u1ee3t s\u1ee9c ch\u1ee9a."
            )
        return AssistantSummary(
            headline=(
                f"K\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng {horizon} ng\u00e0y"
                if horizon else "K\u1ebf ho\u1ea1ch nh\u1eadp h\u00e0ng"
            ),
            summary=summary,
            key_points=points[:3],
            warning_summary=(
                "M\u1ed9t s\u1ed1 ch\u1ec9 s\u1ed1 r\u1ee7i ro ch\u01b0a \u0111\u1ee7 d\u1eef li\u1ec7u \u0111\u1ec3 \u0111\u00e1nh gi\u00e1 \u0111\u1ea7y \u0111\u1ee7."
                if limitations else None
            ),
            source="deterministic_fallback",
            grounded=True,
        )

    def summarize(self, brief: DecisionBriefFacts, facts: list[SemanticFact]) -> AssistantSummary:
        fallback = self.deterministic_fallback(brief, facts)
        if not brief.recommendation.available or not self.llm_provider or not self.llm_provider.available:
            return fallback
        request_context: dict[str, Any] = {"decision_run_id": brief.decision_run_id}
        failure_stage = LLMFailureStage.UNKNOWN.value
        try:
            evidence, structured = self._context(brief, facts)
            if not structured:
                return fallback
            payload = {"language": "vi", "evidence": structured}
            raw = self._run_gateway(payload, request_context)
            try:
                typed = DecisionOverallSummaryLLMResponse.model_validate(raw)
            except Exception as exc:
                failure_stage = LLMFailureStage.SCHEMA_VALIDATION.value
                raise ValueError("overall_summary_schema_validation_failed") from exc
            claims = [typed.headline, typed.summary, *typed.key_points]
            if typed.warning_summary is not None:
                claims.append(typed.warning_summary)
            guard_raw = {
                "answer": typed.summary.text,
                "claims": [claim.model_dump(mode="json") for claim in claims],
                "used_evidence_ids": typed.used_evidence_ids,
            }
            # Reuse the established claim/evidence/numeric/entity/causal guard.
            try:
                self._guard._guard(
                    guard_raw, structured, evidence.items, brief, "vi", "simple", "OVERALL_SUMMARY",
                )
            except Exception:
                failure_stage = LLMFailureStage.GROUNDING.value
                raise
            return AssistantSummary(
                headline=typed.headline.text,
                summary=typed.summary.text,
                key_points=[item.text for item in typed.key_points],
                warning_summary=typed.warning_summary.text if typed.warning_summary else None,
                source="llm",
                grounded=True,
            )
        except Exception as exc:
            details = getattr(exc, "details", {})
            if failure_stage == LLMFailureStage.UNKNOWN.value and isinstance(details, dict):
                failure_stage = str(details.get("failure_stage") or failure_stage)
            profile_getter = getattr(self.llm_provider, "task_profile", None)
            profile = profile_getter(LLMTask.PLAN_SUMMARY) if callable(profile_getter) else None
            metadata = request_context.get("openrouter_metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            logger.warning(
                "overall_summary_fallback decision_run_id=%s task=%s configured_model=%s resolved_provider=%s failure_stage=%s reason=%s",
                brief.decision_run_id, LLMTask.PLAN_SUMMARY.value,
                getattr(profile, "model", None), metadata.get("resolved_provider"),
                failure_stage, type(exc).__name__,
            )
            return fallback

    def _run_gateway(self, payload: dict[str, Any], request_context: dict[str, Any]) -> dict[str, Any]:
        coroutine = self.llm_provider.generate_json(
            SYSTEM_PROMPT, payload, task=LLMTask.PLAN_SUMMARY, request_context=request_context,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coroutine)).result()
        return asyncio.run(coroutine)

    def _context(self, brief: DecisionBriefFacts, facts: list[SemanticFact]):
        evidence = self._adapter._evidence(brief, semantic_facts=facts)
        evidence_by_fact_id = {
            str(item.payload.get("semantic_fact_id")): item
            for item in evidence.items
            if item.evidence_type.startswith("semantic_") and item.payload.get("semantic_fact_id")
        }
        records: list[dict[str, Any]] = []
        for fact in self._select_facts(facts):
            item = evidence_by_fact_id.get(fact.fact_id)
            if item is None:
                continue
            record = {
                "evidence_id": item.evidence_id,
                "evidence_ids": [item.evidence_id],
                "type": fact.fact_type,
                "classification": fact.classification.value,
                **fact.entities,
                **fact.values,
            }
            self._add_display_values(record)
            records.append(record)
        return evidence, records

    @staticmethod
    def _select_facts(facts: list[SemanticFact]) -> list[SemanticFact]:
        overview = [fact for fact in facts if fact.fact_type == "PLAN_OVERVIEW"]
        demand = sorted(
            (fact for fact in facts if fact.fact_type == "DEMAND_HORIZON_SUMMARY"),
            key=lambda fact: float(fact.values.get("p50_total") or 0), reverse=True,
        )[:3]
        alignment = sorted(
            (fact for fact in facts if fact.fact_type == "DEMAND_ORDER_ALIGNMENT"),
            key=lambda fact: float(fact.values.get("absolute_gap_magnitude") or 0), reverse=True,
        )[:3]
        baseline = sorted(
            (
                fact for fact in facts
                if fact.fact_type == "NO_PLANNED_PURCHASE_BASELINE"
                and fact.scope is SemanticFactScope.INGREDIENT
                and float(fact.values.get("shortage_quantity") or 0) > 0
            ),
            key=lambda fact: float(fact.values.get("shortage_quantity") or 0), reverse=True,
        )[:3]
        risk = sorted(
            (fact for fact in facts if fact.classification is SemanticFactClassification.RISK_SIGNAL),
            key=lambda fact: max(
                float(fact.values.get("shortage_quantity") or 0),
                float(fact.values.get("capacity_violation_quantity") or 0),
            ), reverse=True,
        )[:3]
        limitations = sorted(
            (fact for fact in facts if fact.classification is SemanticFactClassification.LIMITATION),
            key=lambda fact: fact.fact_type,
        )[:3]
        selected_risk = [fact for fact in facts if fact.fact_type == "SELECTED_PLAN_RISK_METRICS"]
        return [*overview, *selected_risk, *demand, *alignment, *baseline, *risk, *limitations]

    @staticmethod
    def _add_display_values(record: dict[str, Any]) -> None:
        cost = record.get("total_purchase_cost")
        if isinstance(cost, (int, float)):
            if cost >= 1_000_000:
                display_value = round(float(cost) / 1_000_000, 2)
                record["total_purchase_cost_display"] = f"{_vi_number(display_value)} tri\u1ec7u \u0111\u1ed3ng"
                record["total_purchase_cost_display_value"] = display_value
            else:
                record["total_purchase_cost_display"] = f"{_vi_number(float(cost), 0)} \u0111\u1ed3ng"
                record["total_purchase_cost_display_value"] = float(cost)
        rate = record.get("expected_fill_rate")
        if isinstance(rate, (int, float)):
            display_value = round(float(rate) * 100, 1)
            record["expected_fill_rate_display"] = f"{_vi_number(display_value, 1)}%"
            record["expected_fill_rate_display_value"] = display_value
