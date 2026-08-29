"""Deterministic communication priorities for grounded LLM wording.

The plan is presentation metadata only: it never changes a forecast, risk,
recommendation, or the set of facts that can be grounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommunicationPlan:
    decision: list[str]
    main_attention: list[str]
    limitation: list[str]
    supporting: list[str]

    def as_payload(self, *, presentation_roles: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = {
            "decision": self.decision,
            "main_risk": self.main_attention,
            "main_attention": self.main_attention,
            "limitation": self.limitation,
            "supporting": self.supporting,
            "causal_allowed": False,
            "authorized_evidence_ids": self.evidence_ids,
        }
        if presentation_roles is not None:
            # Additive, internal instruction metadata for model-facing roles.
            # The original ID lists remain the authority and compatibility shape.
            payload["presentation_roles"] = presentation_roles
        return payload

    @property
    def main_risk(self) -> list[str]:
        return self.main_attention

    @property
    def evidence_ids(self) -> list[str]:
        return list(dict.fromkeys([
            *self.decision, *self.main_attention, *self.limitation, *self.supporting,
        ]))


def _ids(records: list[dict[str, Any]], predicate, limit: int | None = None) -> list[str]:
    values = [str(record["evidence_id"]) for record in records if predicate(record)]
    return values if limit is None else values[:limit]


def summary_communication_plan(records: list[dict[str, Any]]) -> CommunicationPlan:
    """Decision -> operational attention -> important limitation -> context."""
    decision = _ids(records, lambda item: item.get("type") == "PLAN_OVERVIEW", 1)
    # Metrics describe the selected plan and are useful context, not a reason to
    # replace the plan decision selected by the backend.
    supporting = _ids(records, lambda item: item.get("type") == "SELECTED_PLAN_RISK_METRICS", 1)
    operational = [item for item in records if item.get("type") == "INGREDIENT_OPERATIONAL_RISK"]

    def risk_key(item: dict[str, Any]):
        return (
            item.get("first_stockout_date") is None,
            str(item.get("first_stockout_date") or "9999-12-31"),
            float(item.get("fill_rate")) if item.get("fill_rate") is not None else float("inf"),
            -float(item.get("shortage_quantity") or 0),
            -int(item.get("stockout_event_count") or 0),
            str(item.get("ingredient_name") or item.get("ingredient_id") or ""),
            str(item.get("ingredient_id") or ""),
        )

    risk = [str(item["evidence_id"]) for item in sorted(operational, key=risk_key)[:1]]
    if not risk:
        risk = _ids(records, lambda item: item.get("classification") == "RISK_SIGNAL", 1)
    limitation = _ids(records, lambda item: item.get("classification") == "LIMITATION", 1)
    selected = set(decision + supporting + risk + limitation)
    if not supporting:
        supporting = _ids(records, lambda item: item.get("evidence_id") not in selected and item.get("type") == "DEMAND_ORDER_ALIGNMENT", 1)
        if not supporting:
            supporting = _ids(records, lambda item: item.get("evidence_id") not in selected and item.get("type") in {"DEMAND_HORIZON_SUMMARY", "NO_PLANNED_PURCHASE_BASELINE"}, 1)
    return CommunicationPlan(decision, risk, limitation, supporting)


def narrative_communication_plan(records: list[dict[str, Any]], intent: str) -> CommunicationPlan:
    """Use existing retrieval intent to select answer-first evidence deterministically."""
    upper_intent = str(intent).upper()
    if "STRATEGY_COMPARISON" in upper_intent:
        primary = _ids(records, lambda item: item.get("type") in {"STRATEGY_COMPARISON", "STRATEGY_SELECTION_PROOF"})
        supporting = _ids(records, lambda item: item.get("type") == "STRATEGY_CANDIDATE_METRICS")
    elif any(token in upper_intent for token in ("NEED", "WHY", "EXPLAIN")):
        primary = _ids(records, lambda item: item.get("type") == "PROCUREMENT_REASON" or item.get("classification") == "CAUSAL", 1)
        # Deliberately leave primary empty when no causal fact exists. The
        # existing prompt/fallback then says that a cause cannot be confirmed.
        supporting = _ids(records, lambda item: item.get("type") in {"PROCUREMENT_QUANTITY", "DEMAND_HORIZON_SUMMARY"}, 2)
    elif "QUANTITY" in upper_intent:
        primary = _ids(records, lambda item: item.get("type") == "PROCUREMENT_QUANTITY", 1)
        supporting = _ids(records, lambda item: item.get("type") == "DEMAND_HORIZON_SUMMARY", 1)
    elif "DEMAND" in upper_intent:
        primary = _ids(records, lambda item: item.get("type") == "DEMAND_HORIZON_SUMMARY", 1)
        supporting = _ids(records, lambda item: item.get("type") == "DEMAND_DAILY", 2)
    else:
        primary = _ids(records, lambda item: item.get("type") in {"PROCUREMENT_QUANTITY", "PLAN_OVERVIEW", "DEMAND_HORIZON_SUMMARY"}, 1)
        supporting = _ids(records, lambda item: item.get("type") in {"DEMAND_HORIZON_SUMMARY", "PROCUREMENT_QUANTITY", "DEMAND_ORDER_ALIGNMENT"}, 2)
    attention = _ids(records, lambda item: item.get("classification") == "RISK_SIGNAL" or item.get("type") == "RISK", 2)
    limitation = _ids(records, lambda item: item.get("classification") == "LIMITATION", 1)
    selected = set(primary + supporting + attention + limitation)
    supporting.extend(_ids(records, lambda item: item.get("evidence_id") not in selected, 2))
    return CommunicationPlan(primary, attention, limitation, supporting)


def what_if_communication_plan(records: list[dict[str, Any]]) -> CommunicationPlan:
    mutation = _ids(records, lambda item: item.get("fact_type") == "WHAT_IF_MUTATION", 1)
    outcomes = _ids(records, lambda item: item.get("fact_type") in {
        "WHAT_IF_PURCHASE_COST_DELTA", "WHAT_IF_FEASIBILITY_CHANGE", "WHAT_IF_FILL_RATE_DELTA",
        "WHAT_IF_STOCKOUT_PROBABILITY_DELTA", "WHAT_IF_ORDER_CHANGE", "WHAT_IF_STRATEGY_CHANGE",
        "WHAT_IF_RISK_CHANGE",
    }, 3)
    return CommunicationPlan(mutation, outcomes[:2], [], outcomes[2:])
