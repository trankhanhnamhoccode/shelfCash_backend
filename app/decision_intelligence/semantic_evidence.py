"""Deterministic semantic facts derived from persisted Decision Run data.

This module deliberately distinguishes observations, derivations, causal facts,
risk signals, and limitations.  It never infers causal facts from coincident
numbers or legacy reason codes.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.decision_intelligence.contracts import DecisionBriefFacts


class SemanticFactScope(str, Enum):
    RUN = "RUN"
    INGREDIENT = "INGREDIENT"
    SUPPLIER = "SUPPLIER"
    STRATEGY = "STRATEGY"


class SemanticFactClassification(str, Enum):
    OBSERVATION = "OBSERVATION"
    DERIVED = "DERIVED"
    CAUSAL = "CAUSAL"
    RISK_SIGNAL = "RISK_SIGNAL"
    LIMITATION = "LIMITATION"
    UNKNOWN = "UNKNOWN"


class SemanticFactProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_module: str
    source_path: str
    source_field: str | None = None
    semantics_note: str | None = None


class SemanticFact(BaseModel):
    """Stable internal contract consumed by evidence/retrieval/narrative paths."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    fact_type: str
    decision_run_id: str
    classification: SemanticFactClassification
    scope: SemanticFactScope
    entities: dict[str, str] = Field(default_factory=dict)
    values: dict[str, JsonValue] = Field(default_factory=dict)
    source_evidence_ids: list[str] = Field(default_factory=list)
    provenance: SemanticFactProvenance


_LIMITATION_CODES = {
    "AGGREGATE_MODEL_COUNTS_UNKNOWN_EXPIRY_LOT",
    "CAPACITY_NOT_EVALUATED",
    "INBOUND_EXPIRY_NOT_EVALUATED",
    "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED",
    "RISK_METRIC_NOT_AVAILABLE",
    "SHORTAGE_CONSEQUENCE_NOT_CONFIGURED",
    "SHORTAGE_COST_FALLBACK_USED",
    "UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS",
    "UNWEIGHTED_SERVICE_PROBABILITY_NOT_EVALUATED",
    "UNWEIGHTED_STOCKOUT_PROBABILITY_NOT_EVALUATED",
}
_RISK_SIGNAL_CODES = {"STRESS_SHORTAGE_OBSERVED", "STRESS_CAPACITY_VIOLATION"}

# These are critic findings with explicit, deterministic semantics.  They are
# kept separate from constraint codes carrying an offer ID (for example
# ``MOQ:<offer_id>``), whose business-facing meaning is not safe to infer here.
_LIMITATION_FINDING_CODES = {
    "UNKNOWN_EXPIRY",
    "M4_SIMULATION_FAILED",
    "M4_ACCOUNTING_INVALID",
    "STRESS_ACCOUNTING_INVALID",
    "CANDIDATE_MODEL_MISMATCH",
}
_RISK_FINDING_CODES = {
    "CAPACITY_CONSEQUENCE",
    "EXACT_SIMULATION_SAFETY_FLOOR",
    "RISK_CONSTRAINT_VIOLATION",
    "SERVICE_LEVEL_REQUIREMENT",
}


def _source_id(source_path: str) -> str:
    return f"source:{source_path}"


def _fact_id(
    decision_run_id: str,
    fact_type: str,
    scope: SemanticFactScope,
    entities: dict[str, str],
    discriminator: str = "",
) -> str:
    identity = {
        "decision_run_id": decision_run_id,
        "fact_type": fact_type,
        "scope": scope.value,
        "entities": dict(sorted(entities.items())),
        "discriminator": discriminator,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"sf-{fact_type.lower().replace('_', '-')}-{digest}"


def _date(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _number(value: object) -> float | None:
    return None if value is None else float(value)


class DecisionSemanticEvidenceBuilder:
    """Build facts only from a brief and its same-run persisted package."""

    def build(
        self,
        brief: DecisionBriefFacts,
        package: dict[str, Any] | None = None,
    ) -> list[SemanticFact]:
        facts: list[SemanticFact] = []
        facts.extend(self._plan_overview_facts(brief))
        demand_facts = self._demand_facts(brief)
        facts.extend(demand_facts)
        facts.extend(self._procurement_facts(brief))
        facts.extend(self._alignment_facts(brief, demand_facts))
        facts.extend(self._selected_risk_facts(brief))
        if isinstance(package, dict):
            facts.extend(self._operational_risk_facts(brief, package))
            facts.extend(self._baseline_facts(brief, package))
            facts.extend(self._stress_facts(brief, package))
            facts.extend(self._warning_facts(brief, package))
            facts.extend(self._strategy_facts(brief, package))
        return sorted(facts, key=lambda item: item.fact_id)

    def _operational_risk_facts(self, brief: DecisionBriefFacts, package: dict[str, Any]) -> list[SemanticFact]:
        """Expose already-computed selected-plan ingredient risks for ranking."""
        metrics = package.get("business_metrics")
        deterministic = metrics.get("deterministic") if isinstance(metrics, dict) else None
        rows = deterministic.get("ingredient_metrics") if isinstance(deterministic, dict) else None
        if not isinstance(rows, list):
            return []
        names = {row.ingredient_id: row.ingredient_name for row in brief.procurement_rows if row.ingredient_name}
        names.update({row.ingredient_id: row.ingredient_name for row in brief.ingredient_demand if row.ingredient_name})
        facts: list[SemanticFact] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("ingredient_id"):
                continue
            shortage = _number(row.get("shortage_quantity")) or 0.0
            events = int(row.get("stockout_event_count") or 0)
            first = _date(row.get("first_stockout_date"))
            if shortage <= 0 and events <= 0 and first is None:
                continue
            ingredient_id = str(row["ingredient_id"])
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "INGREDIENT_OPERATIONAL_RISK", SemanticFactScope.INGREDIENT, {"ingredient_id": ingredient_id}),
                fact_type="INGREDIENT_OPERATIONAL_RISK",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.RISK_SIGNAL,
                scope=SemanticFactScope.INGREDIENT,
                entities={"ingredient_id": ingredient_id},
                values={
                    "ingredient_name": names.get(ingredient_id), "unit": row.get("unit"),
                    "fill_rate": _number(row.get("fill_rate")), "shortage_quantity": shortage,
                    "first_stockout_date": first, "stockout_event_count": events,
                },
                source_evidence_ids=[_source_id(f"package.business_metrics.deterministic.ingredient_metrics[{index}]")],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json", source_module="app.services.business_metrics_service",
                    source_path=f"package.business_metrics.deterministic.ingredient_metrics[{index}]",
                    source_field="Exact FEFO selected-plan ingredient metric",
                ),
            ))
        return facts

    def _plan_overview_facts(self, brief: DecisionBriefFacts) -> list[SemanticFact]:
        return [SemanticFact(
            fact_id=_fact_id(brief.decision_run_id, "PLAN_OVERVIEW", SemanticFactScope.RUN, {}),
            fact_type="PLAN_OVERVIEW",
            decision_run_id=brief.decision_run_id,
            classification=SemanticFactClassification.OBSERVATION,
            scope=SemanticFactScope.RUN,
            values={
                "recommendation_available": brief.recommendation.available,
                "strategy": brief.recommendation.strategy,
                "horizon_days": brief.forecast.horizon_days,
                "ordered_ingredient_count": len({row.ingredient_id for row in brief.procurement_rows}),
                "total_purchase_cost": _number(brief.recommendation.total_purchase_cost),
            },
            source_evidence_ids=[_source_id("package.recommended_strategy"), _source_id("package.recommended_plan.items")],
            provenance=SemanticFactProvenance(
                source_type="DecisionRun.package_json",
                source_module="app.decision_intelligence.semantic_evidence",
                source_path="package.recommended_strategy + package.recommended_plan.items + package.business_metrics",
                source_field="recommended_strategy,projected_purchase_cost",
            ),
        )]

    def _demand_facts(self, brief: DecisionBriefFacts) -> list[SemanticFact]:
        grouped: dict[str, list] = defaultdict(list)
        for demand in brief.ingredient_demand:
            grouped[demand.ingredient_id].append(demand)
        facts: list[SemanticFact] = []
        for ingredient_id, rows in sorted(grouped.items()):
            ordered = sorted(rows, key=lambda row: _date(row.target_date))
            p50_rows = [row for row in ordered if row.p50 is not None]
            if not ordered or not p50_rows:
                continue
            entities = {"ingredient_id": ingredient_id}
            source_paths = [
                f"package.ingredient_demand[ingredient_id={ingredient_id};target_date={_date(row.target_date)}]"
                for row in ordered
            ]
            peak = max(p50_rows, key=lambda row: (float(row.p50), _date(row.target_date)))
            values: dict[str, JsonValue] = {
                "unit": ordered[0].unit,
                "ingredient_name": ordered[0].ingredient_name,
                "period_start": _date(ordered[0].target_date),
                "period_end": _date(ordered[-1].target_date),
                "p25_total": float(sum(row.p25 or 0 for row in ordered)),
                "p50_total": float(sum(row.p50 or 0 for row in ordered)),
                "p75_total": float(sum(row.p75 or 0 for row in ordered)),
                "daily_p50_min": float(min(row.p50 for row in p50_rows if row.p50 is not None)),
                "daily_p50_max": float(max(row.p50 for row in p50_rows if row.p50 is not None)),
                "peak_date": _date(peak.target_date),
                "peak_p50": float(peak.p50),
                "aggregation_method": "sum_daily_quantiles",
            }
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "DEMAND_HORIZON_SUMMARY", SemanticFactScope.INGREDIENT, entities),
                fact_type="DEMAND_HORIZON_SUMMARY",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.DERIVED,
                scope=SemanticFactScope.INGREDIENT,
                entities=entities,
                values=values,
                source_evidence_ids=[_source_id(path) for path in source_paths],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="app.decision_intelligence.semantic_evidence",
                    source_path="package.ingredient_demand",
                    source_field="p25,p50,p75",
                    semantics_note="Daily quantiles are summed; totals are not an independent horizon distribution quantile.",
                ),
            ))
        return facts

    def _procurement_facts(self, brief: DecisionBriefFacts) -> list[SemanticFact]:
        facts: list[SemanticFact] = []
        for index, row in enumerate(brief.procurement_rows):
            entities = {"ingredient_id": row.ingredient_id}
            if row.supplier_id:
                entities["supplier_id"] = row.supplier_id
            source_path = f"package.recommended_plan.items[{index}]"
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "PROCUREMENT_QUANTITY", SemanticFactScope.INGREDIENT, entities, str(index)),
                fact_type="PROCUREMENT_QUANTITY",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.OBSERVATION,
                scope=SemanticFactScope.INGREDIENT,
                entities=entities,
                values={
                    "quantity": float(row.quantity),
                    "unit": row.unit,
                    "purchase_cost": _number(row.purchase_cost),
                    "ingredient_name": row.ingredient_name,
                    "supplier_name": row.supplier_name,
                },
                source_evidence_ids=[_source_id(source_path)],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="app.decision_intelligence.semantic_evidence",
                    source_path=source_path,
                    source_field="order_quantity,unit,purchase_cost",
                ),
            ))
        return facts

    def _alignment_facts(
        self,
        brief: DecisionBriefFacts,
        demand_facts: list[SemanticFact],
    ) -> list[SemanticFact]:
        summaries = {fact.entities["ingredient_id"]: fact for fact in demand_facts}
        orders: dict[str, list] = defaultdict(list)
        for row in brief.procurement_rows:
            orders[row.ingredient_id].append(row)
        facts: list[SemanticFact] = []
        for ingredient_id, rows in sorted(orders.items()):
            summary = summaries.get(ingredient_id)
            if summary is None:
                continue
            unit = summary.values.get("unit")
            if not isinstance(unit, str) or any(row.unit != unit for row in rows):
                continue
            p50_total = summary.values.get("p50_total")
            if not isinstance(p50_total, (int, float)):
                continue
            order_total = float(sum(row.quantity for row in rows))
            gap = order_total - float(p50_total)
            entities = {"ingredient_id": ingredient_id}
            name = summary.values.get("ingredient_name")
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "DEMAND_ORDER_ALIGNMENT", SemanticFactScope.INGREDIENT, entities),
                fact_type="DEMAND_ORDER_ALIGNMENT",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.DERIVED,
                scope=SemanticFactScope.INGREDIENT,
                entities=entities,
                values={
                    "unit": unit,
                    "p50_total": float(p50_total),
                    "order_quantity_total": order_total,
                    "absolute_gap": gap,
                    "absolute_gap_magnitude": abs(gap),
                    "relative_gap": None if float(p50_total) == 0 else gap / float(p50_total),
                    "order_line_count": len(rows),
                    "ingredient_name": name,
                },
                source_evidence_ids=[*summary.source_evidence_ids, *[
                    _source_id(f"package.recommended_plan.items[{index}]")
                    for index, row in enumerate(brief.procurement_rows) if row.ingredient_id == ingredient_id
                ]],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="app.decision_intelligence.semantic_evidence",
                    source_path="package.ingredient_demand + package.recommended_plan.items",
                    source_field="p50 + order_quantity",
                    semantics_note="A numerical alignment fact; it does not establish a procurement cause.",
                ),
            ))
        return facts

    def _selected_risk_facts(self, brief: DecisionBriefFacts) -> list[SemanticFact]:
        values = {
            "stockout_probability": _number(brief.risk.stockout_probability),
            "expected_fill_rate": _number(brief.risk.expected_fill_rate),
            "shortage_quantity": _number(brief.risk.shortage_quantity),
            "waste_quantity": _number(brief.risk.waste_quantity),
        }
        if not any(value is not None for value in values.values()):
            return []
        return [SemanticFact(
            fact_id=_fact_id(brief.decision_run_id, "SELECTED_PLAN_RISK_METRICS", SemanticFactScope.RUN, {}),
            fact_type="SELECTED_PLAN_RISK_METRICS",
            decision_run_id=brief.decision_run_id,
            classification=SemanticFactClassification.OBSERVATION,
            scope=SemanticFactScope.RUN,
            values=values,
            source_evidence_ids=[_source_id("package.business_metrics")],
            provenance=SemanticFactProvenance(
                source_type="DecisionRun.package_json",
                source_module="app.decision_intelligence.semantic_evidence",
                source_path="package.business_metrics",
            ),
        )]

    def _baseline_facts(self, brief: DecisionBriefFacts, package: dict[str, Any]) -> list[SemanticFact]:
        baseline = package.get("inventory_risk")
        if not isinstance(baseline, dict):
            return []
        facts: list[SemanticFact] = []
        for result_index, result in enumerate(baseline.get("results", [])):
            if not isinstance(result, dict):
                continue
            scenario_id = str(result.get("scenario_id") or result_index)
            run_entities = {"scenario_id": scenario_id}
            run_path = f"package.inventory_risk.results[{result_index}]"
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "NO_PLANNED_PURCHASE_BASELINE", SemanticFactScope.RUN, run_entities),
                fact_type="NO_PLANNED_PURCHASE_BASELINE",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.OBSERVATION,
                scope=SemanticFactScope.RUN,
                entities=run_entities,
                values={
                    "planned_purchases_from_decision_run_excluded": True,
                    "existing_inbound_retained": True,
                    "simulation_start_date": _date(result.get("simulation_start_date")),
                    "simulation_end_date": _date(result.get("simulation_end_date")),
                },
                source_evidence_ids=[_source_id(run_path)],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="app.services.decision.adapters.procurement_adapter",
                    source_path=run_path,
                    source_field="baseline Exact FEFO before planned inbound",
                    semantics_note="Existing inbound/open purchase orders remain in the baseline simulation.",
                ),
            ))
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            for key_index, key in enumerate(summary.get("by_key", [])):
                if not isinstance(key, dict) or not key.get("ingredient_id"):
                    continue
                entities = {"ingredient_id": str(key["ingredient_id"]), "scenario_id": scenario_id}
                facts.append(SemanticFact(
                    fact_id=_fact_id(brief.decision_run_id, "NO_PLANNED_PURCHASE_BASELINE", SemanticFactScope.INGREDIENT, entities),
                    fact_type="NO_PLANNED_PURCHASE_BASELINE",
                    decision_run_id=brief.decision_run_id,
                    classification=SemanticFactClassification.OBSERVATION,
                    scope=SemanticFactScope.INGREDIENT,
                    entities=entities,
                    values={
                        "planned_purchases_from_decision_run_excluded": True,
                        "existing_inbound_retained": True,
                        "unit": key.get("unit"),
                        "total_demand": _number(key.get("total_demand")),
                        "fulfilled_quantity": _number(key.get("fulfilled_quantity")),
                        "shortage_quantity": _number(key.get("shortage_quantity")),
                        "ending_inventory": _number(key.get("ending_inventory")),
                        "fill_rate": _number(key.get("fill_rate")),
                        "projected_stockout_date": None if key.get("projected_stockout_date") is None else _date(key.get("projected_stockout_date")),
                        "stockout_event_count": key.get("stockout_event_count"),
                        "waste_quantity": _number(key.get("explicit_waste_quantity")),
                    },
                    source_evidence_ids=[_source_id(f"{run_path}.summary.by_key[{key_index}]")],
                    provenance=SemanticFactProvenance(
                        source_type="DecisionRun.package_json",
                        source_module="app.services.decision.adapters.procurement_adapter",
                        source_path=f"{run_path}.summary.by_key[{key_index}]",
                        source_field="Exact FEFO baseline summary",
                        semantics_note="Planned purchases from this Decision Run are excluded; existing inbound remains.",
                    ),
                ))
        return facts

    def _stress_facts(self, brief: DecisionBriefFacts, package: dict[str, Any]) -> list[SemanticFact]:
        stress = package.get("stress_tests")
        if not isinstance(stress, dict):
            return []
        facts: list[SemanticFact] = []
        for result_index, result in enumerate(stress.get("results", [])):
            if not isinstance(result, dict):
                continue
            scenario_id = str(result.get("scenario_id") or result_index)
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            for key_index, key in enumerate(summary.get("by_key", [])):
                if not isinstance(key, dict) or not key.get("ingredient_id"):
                    continue
                shortage = _number(key.get("shortage_quantity")) or 0.0
                capacity = _number(key.get("capacity_violation_quantity")) or 0.0
                for fact_type, value_key, value in (
                    ("STRESS_SHORTAGE_OBSERVED", "shortage_quantity", shortage),
                    ("STRESS_CAPACITY_VIOLATION", "capacity_violation_quantity", capacity),
                ):
                    if value <= 0:
                        continue
                    entities = {"ingredient_id": str(key["ingredient_id"]), "scenario_id": scenario_id}
                    facts.append(SemanticFact(
                        fact_id=_fact_id(brief.decision_run_id, fact_type, SemanticFactScope.INGREDIENT, entities),
                        fact_type=fact_type,
                        decision_run_id=brief.decision_run_id,
                        classification=SemanticFactClassification.RISK_SIGNAL,
                        scope=SemanticFactScope.INGREDIENT,
                        entities=entities,
                        values={"unit": key.get("unit"), value_key: value},
                        source_evidence_ids=[_source_id(f"package.stress_tests.results[{result_index}].summary.by_key[{key_index}]")],
                        provenance=SemanticFactProvenance(
                            source_type="DecisionRun.package_json",
                            source_module="shelfcash_core.optimization.critic",
                            source_path=f"package.stress_tests.results[{result_index}].summary.by_key[{key_index}]",
                        ),
                    ))
        return facts

    def _warning_facts(self, brief: DecisionBriefFacts, package: dict[str, Any]) -> list[SemanticFact]:
        warnings: list[tuple[str, str, str | None]] = []
        critic = package.get("critic")
        critic_warnings = critic.get("warnings", []) if isinstance(critic, dict) else []
        for path, values in (
            ("package.warnings", package.get("warnings", [])),
            ("package.critic.warnings", critic_warnings),
        ):
            if isinstance(values, list):
                warnings.extend((str(code), path, None) for code in values if isinstance(code, str))
        if isinstance(critic, dict) and isinstance(critic.get("findings"), list):
            for index, finding in enumerate(critic["findings"]):
                if isinstance(finding, dict) and isinstance(finding.get("code"), str):
                    warnings.append((
                        str(finding["code"]),
                        f"package.critic.findings[{index}]",
                        str(finding.get("severity")) if finding.get("severity") is not None else None,
                    ))
        facts: list[SemanticFact] = []
        for code, path, raw_severity in sorted(set(warnings)):
            classification = (
                SemanticFactClassification.LIMITATION
                if code in _LIMITATION_CODES | _LIMITATION_FINDING_CODES
                else SemanticFactClassification.RISK_SIGNAL
                if code in _RISK_SIGNAL_CODES | _RISK_FINDING_CODES
                else SemanticFactClassification.UNKNOWN
            )
            facts.append(SemanticFact(
                fact_id=_fact_id(
                    brief.decision_run_id,
                    code,
                    SemanticFactScope.RUN,
                    {},
                    discriminator=path,
                ),
                fact_type=code,
                decision_run_id=brief.decision_run_id,
                classification=classification,
                scope=SemanticFactScope.RUN,
                values={"code": code, "raw_severity": raw_severity},
                source_evidence_ids=[_source_id(path)],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="shelfcash_core.optimization.critic",
                    source_path=path,
                    source_field="warnings/findings",
                ),
            ))
        return facts

    def _strategy_facts(self, brief: DecisionBriefFacts, package: dict[str, Any]) -> list[SemanticFact]:
        """Expose candidate observations and only a reproducible selection proof."""
        strategies = package.get("strategies")
        if not isinstance(strategies, dict):
            return []

        candidates: dict[str, dict[str, Any]] = {}
        facts: list[SemanticFact] = []
        for strategy, candidate in sorted(strategies.items()):
            if not isinstance(candidate, dict):
                continue
            strategy_id = str(candidate.get("strategy") or strategy).lower()
            if not strategy_id:
                continue
            metrics = candidate.get("business_metrics")
            metrics = metrics if isinstance(metrics, dict) else {}
            probabilistic = metrics.get("probabilistic")
            probabilistic = probabilistic if isinstance(probabilistic, dict) else {}
            critic = candidate.get("critic")
            critic = critic if isinstance(critic, dict) else {}
            findings = critic.get("findings") if isinstance(critic.get("findings"), list) else []
            warnings = critic.get("warnings") if isinstance(critic.get("warnings"), list) else []
            stress = candidate.get("stress_tests")
            stress = stress if isinstance(stress, dict) else {}
            stress_shortage, stress_capacity = _candidate_stress_observations(stress)
            source_path = f"package.strategies[{strategy_id}]"
            values: dict[str, JsonValue] = {
                "strategy": strategy_id,
                "selected": strategy_id == brief.recommendation.strategy,
                "feasible": bool(candidate.get("is_feasible")),
                # This exact candidate-plan cost is the selector input.
                "purchase_cost": _number(candidate.get("purchase_cost")),
                "expected_fill_rate": _number(probabilistic.get("expected_fill_rate")),
                "stockout_probability": _number(probabilistic.get("stockout_probability")),
                "risk_evaluation_status": probabilistic.get("status"),
                "risk_evaluation_method": probabilistic.get("method"),
                "risk_evaluation_metric_source": probabilistic.get("metric_source"),
                "hard_violation_count": len(findings),
                "warning_count": len(warnings),
                "stress_shortage_observed": stress_shortage,
                "stress_capacity_violation": stress_capacity,
            }
            candidates[strategy_id] = values
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "STRATEGY_CANDIDATE_METRICS", SemanticFactScope.STRATEGY, {"strategy": strategy_id}),
                fact_type="STRATEGY_CANDIDATE_METRICS",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.OBSERVATION,
                scope=SemanticFactScope.STRATEGY,
                entities={"strategy": strategy_id},
                values=values,
                source_evidence_ids=[
                    _source_id(f"{source_path}.purchase_cost"),
                    _source_id(f"{source_path}.business_metrics"),
                    _source_id(f"{source_path}.critic"),
                ],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="app.services.decision_planning_service",
                    source_path=source_path,
                    source_field="purchase_cost,business_metrics,critic,stress_tests",
                    semantics_note="Candidate-specific metrics from the same decision-run evaluation.",
                ),
            ))

        selected = brief.recommendation.strategy
        selected_values = candidates.get(selected or "")
        if selected and selected_values:
            for alternative, alternative_values in sorted(candidates.items()):
                if alternative == selected:
                    continue
                comparison_values = _strategy_delta_values(selected, alternative, selected_values, alternative_values)
                if not any(value is not None for key, value in comparison_values.items() if key.endswith("_delta")):
                    continue
                facts.append(SemanticFact(
                    fact_id=_fact_id(
                        brief.decision_run_id, "STRATEGY_COMPARISON", SemanticFactScope.STRATEGY,
                        {"left_strategy": selected, "right_strategy": alternative},
                    ),
                    fact_type="STRATEGY_COMPARISON",
                    decision_run_id=brief.decision_run_id,
                    classification=SemanticFactClassification.DERIVED,
                    scope=SemanticFactScope.STRATEGY,
                    entities={"left_strategy": selected, "right_strategy": alternative},
                    values=comparison_values,
                    source_evidence_ids=[
                        _source_id(f"package.strategies[{selected}]"),
                        _source_id(f"package.strategies[{alternative}]"),
                    ],
                    provenance=SemanticFactProvenance(
                        source_type="DecisionRun.package_json",
                        source_module="app.decision_intelligence.semantic_evidence",
                        source_path=f"package.strategies[{selected}] + package.strategies[{alternative}]",
                        source_field="candidate metrics",
                        semantics_note="Directional deltas are left strategy minus right strategy.",
                    ),
                ))

        selection = package.get("strategy_selection")
        proof = _validated_selection_proof(brief, selection, candidates)
        if proof is not None:
            facts.append(SemanticFact(
                fact_id=_fact_id(brief.decision_run_id, "STRATEGY_SELECTION_PROOF", SemanticFactScope.STRATEGY, {"strategy": selected or ""}),
                fact_type="STRATEGY_SELECTION_PROOF",
                decision_run_id=brief.decision_run_id,
                classification=SemanticFactClassification.CAUSAL,
                scope=SemanticFactScope.STRATEGY,
                entities={"strategy": selected or ""},
                values=proof,
                source_evidence_ids=[
                    _source_id("package.strategy_selection"),
                    *[_source_id(f"package.strategies[{strategy}].purchase_cost") for strategy in proof["eligible_strategies"]],
                ],
                provenance=SemanticFactProvenance(
                    source_type="DecisionRun.package_json",
                    source_module="shelfcash_core.optimization.optimizer",
                    source_path="package.strategy_selection + package.strategies",
                    source_field="recommendation_rule,eligible_candidates,purchase_cost",
                    semantics_note="Selection proof is emitted only after persisted rule inputs reproduce the selected strategy.",
                ),
            ))
        return facts


def _candidate_stress_observations(stress: dict[str, Any]) -> tuple[bool | None, bool | None]:
    results = stress.get("results")
    if not isinstance(results, list):
        return None, None
    shortage = capacity = False
    for result in results:
        summary = result.get("summary") if isinstance(result, dict) else None
        by_key = summary.get("by_key") if isinstance(summary, dict) else None
        if not isinstance(by_key, list):
            continue
        for row in by_key:
            if not isinstance(row, dict):
                continue
            shortage = shortage or ((_number(row.get("shortage_quantity")) or 0) > 0)
            capacity = capacity or ((_number(row.get("capacity_violation_quantity")) or 0) > 0)
    return shortage, capacity


def _strategy_delta_values(
    left: str, right: str, left_values: dict[str, Any], right_values: dict[str, Any],
) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {"left_strategy": left, "right_strategy": right}
    left_cost, right_cost = left_values.get("purchase_cost"), right_values.get("purchase_cost")
    values["purchase_cost_delta"] = round(left_cost - right_cost, 12) if left_cost is not None and right_cost is not None else None
    compatible_risk_basis = (
        left_values.get("risk_evaluation_status") == right_values.get("risk_evaluation_status") == "evaluated"
        and left_values.get("risk_evaluation_metric_source")
        and left_values.get("risk_evaluation_metric_source") == right_values.get("risk_evaluation_metric_source")
    )
    for field in ("expected_fill_rate", "stockout_probability"):
        left_value, right_value = left_values.get(field), right_values.get(field)
        values[f"{field}_delta"] = round(left_value - right_value, 12) if compatible_risk_basis and left_value is not None and right_value is not None else None
    fill_delta = values["expected_fill_rate_delta"]
    values["expected_fill_rate_percentage_point_delta"] = round(fill_delta * 100, 12) if fill_delta is not None else None
    return values


def _validated_selection_proof(
    brief: DecisionBriefFacts, selection: object, candidates: dict[str, dict[str, Any]],
) -> dict[str, JsonValue] | None:
    if not isinstance(selection, dict) or not brief.recommendation.strategy:
        return None
    if selection.get("rule") != "lowest_valid_candidate_cost_then_strategy_name":
        return None
    eligible = sorted(strategy for strategy, values in candidates.items() if values.get("feasible") and values.get("purchase_cost") is not None)
    persisted_eligible = sorted(str(value).lower() for value in selection.get("eligible_candidates", []) if isinstance(value, str))
    selected = brief.recommendation.strategy
    if not eligible or persisted_eligible != eligible or selection.get("selected_strategy") != selected:
        return None
    reproduced = min(eligible, key=lambda strategy: (candidates[strategy]["purchase_cost"], strategy))
    if reproduced != selected:
        return None
    return {
        "selected_strategy": selected,
        "eligible_strategies": eligible,
        "rule": "lowest_valid_candidate_cost_then_strategy_name",
        "selection_metric": "purchase_cost",
        "tie_breaker": "strategy_name_ascending",
    }
