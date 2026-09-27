"""Intent- and entity-scoped business evidence selection for narration."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NarrativeRetrieval:
    intent: str
    target_ingredient_id: str | None
    evidence: list[dict[str, Any]]
    causal_allowed: bool


def resolve_ingredient_id(brief, question: str, explicit_id: str | None = None) -> str | None:
    if explicit_id:
        return explicit_id
    lowered = question.lower()
    candidates = {row.ingredient_id: row.ingredient_name for row in [*brief.procurement_rows, *brief.ingredient_demand] if row.ingredient_name}
    matched = [(len(name), ingredient_id) for ingredient_id, name in candidates.items() if name and name.lower() in lowered]
    return max(matched)[1] if matched else None


def detect_intent(question: str) -> str:
    lowered = question.lower()
    if any(token in lowered for token in ("nếu không", "không nhập", "without purchase", "without ordering", "no new")):
        return "BASELINE"
    if any(token in lowered for token in ("tại sao", "vì sao", "why")) or ("cần nhập" in lowered and not any(token in lowered for token in ("bao nhiêu", "lượng", "quantity"))):
        return "WHY_PROCUREMENT"
    if re.search(r"\b\d{1,2}[/-]\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b", lowered):
        return "DEMAND_DAY"
    if any(token in lowered for token in ("bao nhiêu", "lượng", "quantity")):
        return "PROCUREMENT_QUANTITY"
    if any(token in lowered for token in ("nhu cầu", "demand", "tuần tới", "horizon")):
        return "DEMAND_HORIZON"
    if any(token in lowered for token in ("thiếu", "stockout", "rủi ro", "risk")):
        return "RISK"
    if any(token in lowered for token in ("chiến lược", "strategy", "lean", "balanced", "protected", "an toàn", "cân bằng")):
        return "STRATEGY_COMPARISON"
    return "PLAN"


def retrieve_narrative_evidence(brief, records: list[dict[str, Any]], *, question: str, ingredient_id: str | None, detail_level: str, question_scope: str | None = None) -> NarrativeRetrieval:
    if question_scope == "plan_strategy":
        return _retrieve_plan_strategy(records, question)
    intent = detect_intent(question)
    target = resolve_ingredient_id(brief, question, ingredient_id)
    scoped = [item for item in records if not target or not item.get("ingredient_id") or item.get("ingredient_id") == target]

    def types(*allowed: str) -> list[dict[str, Any]]:
        return [item for item in scoped if item.get("type") in allowed]

    if intent == "WHY_PROCUREMENT":
        # A strategy-selection proof is causal at strategy scope, but it is
        # never procurement causality for an ingredient question.
        causal = types("PROCUREMENT_REASON")
        selected = [*causal[:1], *types("PROCUREMENT_QUANTITY")[:1], *types("DEMAND_HORIZON_SUMMARY")[:1], *types("DEMAND_ORDER_ALIGNMENT")[:1]]
        return NarrativeRetrieval(intent, target, _unique(selected), bool(causal))
    if intent == "PROCUREMENT_QUANTITY":
        return NarrativeRetrieval(intent, target, _unique(types("PROCUREMENT_QUANTITY")[:1]), False)
    if intent == "BASELINE":
        return NarrativeRetrieval(intent, target, _unique(types("NO_PLANNED_PURCHASE_BASELINE")[:1]), False)
    if intent == "DEMAND_DAY":
        date_token = _date_token(question)
        daily = [item for item in types("DEMAND_DAILY") if not date_token or str(item.get("target_date", "")).endswith(date_token)]
        return NarrativeRetrieval(intent, target, _unique(daily[:1]), False)
    if intent == "DEMAND_HORIZON":
        selected = types("DEMAND_HORIZON_SUMMARY")[:1]
        if detail_level != "simple" or "peak" in question.lower():
            selected.extend(types("DEMAND_DAILY")[:2])
        # Alignment is a relevant precomputed comparison for peak-demand
        # ingredient detail and keeps legacy detail responses grounded.
        if "peak" in question.lower():
            selected.extend(types("DEMAND_ORDER_ALIGNMENT")[:1])
        return NarrativeRetrieval(intent, target, _unique(selected), False)
    if intent == "RISK":
        return NarrativeRetrieval(intent, target, _unique(types("INGREDIENT_OPERATIONAL_RISK", "RISK", "STRESS_SHORTAGE_OBSERVED")[:2]), False)
    if intent == "STRATEGY_COMPARISON":
        return NarrativeRetrieval(intent, target, _unique(types("STRATEGY_COMPARISON", "STRATEGY_SELECTION_PROOF", "STRATEGY_CANDIDATE_METRICS")), False)
    return NarrativeRetrieval(intent, target, _unique(types("PLAN_OVERVIEW", "PROCUREMENT_QUANTITY", "SELECTED_PLAN_RISK_METRICS")[:3]), False)


def _retrieve_plan_strategy(records: list[dict[str, Any]], question: str) -> NarrativeRetrieval:
    """Select existing plan/strategy facts without re-inferring broad scope."""
    lowered = question.lower()
    selected = next((str(item.get("strategy")).lower() for item in records if item.get("type") == "PLAN_OVERVIEW" and item.get("strategy")), None)
    mentioned = {name for name in ("lean", "balanced", "protected") if re.search(rf"(?<![a-z0-9]){name}(?![a-z0-9])", lowered)}
    alternatives = mentioned - ({selected} if selected else set())
    tradeoff = any(token in lowered for token in ("trade-off", "trade off", "tradeoff"))
    comparison = len(mentioned) >= 2 or bool(alternatives)
    intent = "PLAN_TRADEOFF" if tradeoff else "STRATEGY_COMPARISON" if comparison else "PLAN_SELECTION"

    overview = [item for item in records if item.get("type") == "PLAN_OVERVIEW"][:1]
    proof = [item for item in records if item.get("type") == "STRATEGY_SELECTION_PROOF"][:1]
    comparisons = [
        item for item in records
        if item.get("type") == "STRATEGY_COMPARISON"
        and (not alternatives or str(item.get("right_strategy", "")).lower() in alternatives)
    ]
    if not alternatives and (tradeoff or intent == "PLAN_SELECTION"):
        comparisons = comparisons[:2]
    metrics_strategies = ({selected} if selected else set()) | alternatives | {
        item.get("right_strategy") for item in comparisons if item.get("right_strategy")
    }
    metrics = [
        item for item in records
        if item.get("type") == "STRATEGY_CANDIDATE_METRICS" and str(item.get("strategy", "")).lower() in metrics_strategies
    ]
    relevant = [*overview, *proof, *comparisons, *metrics]
    if any(token in lowered for token in ("rủi ro", "rui ro", "risk", "chú ý", "chu y", "limitation", "giới hạn", "gioi han")):
        relevant.extend(item for item in records if item.get("type") == "SELECTED_PLAN_RISK_METRICS" or item.get("classification") == "LIMITATION")
    return NarrativeRetrieval(intent, None, _unique(relevant), bool(proof))


def _date_token(question: str) -> str | None:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", question)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", question)
    return f"-{int(match.group(2)):02d}-{int(match.group(1)):02d}" if match else None


def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({str(item["evidence_id"]): item for item in items}.values())
