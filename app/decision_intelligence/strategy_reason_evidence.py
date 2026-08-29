"""Deterministic, internal reason evidence for persisted strategy candidates."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from app.decision_intelligence.strategy_outcomes import StrategyCandidateOutcome, project_strategy_outcomes

Kind = Literal["SELECTION", "NON_SELECTION", "BUSINESS_CONSTRAINT_FAILURE", "TECHNICAL_EVALUATION_FAILURE", "LIMITATION", "WARNING", "EVALUATION_UNAVAILABLE"]
_LIMITATIONS = {"CAPACITY_NOT_EVALUATED", "RISK_METRIC_NOT_AVAILABLE", "MONTE_CARLO_DISABLED"}


@dataclass(frozen=True)
class StrategyReasonEvidence:
    evidence_id: str
    strategy: str
    outcome: str
    reason_code: str
    reason_kind: Kind
    authority_source: str
    values: dict[str, Any]
    availability: Literal["FULL", "CODE_ONLY", "UNAVAILABLE"]
    related_strategy: str | None = None
    provenance: str | None = None


def project_strategy_reason_evidence(package: dict[str, Any]) -> list[StrategyReasonEvidence]:
    outcomes = {item.strategy: item for item in project_strategy_outcomes(package)}
    strategies = package.get("strategies") if isinstance(package.get("strategies"), dict) else {}
    selected = str(package.get("recommended_strategy") or "").lower() or None
    proof = all(item.selector_proof_status == "VERIFIED" for item in outcomes.values())
    eligible = {name.lower(): value for name, value in strategies.items() if isinstance(value, dict) and value.get("is_feasible") and isinstance(value.get("purchase_cost"), (int, float))}
    result: list[StrategyReasonEvidence] = []
    for strategy, outcome in sorted(outcomes.items()):
        candidate = strategies.get(strategy, strategies.get(strategy.upper(), {}))
        candidate = candidate if isinstance(candidate, dict) else {}
        if outcome.outcome in {"SELECTED", "FEASIBLE_NOT_SELECTED"}:
            code = outcome.reason_codes[0]
            if proof and code != "SELECTION_REASON_UNAVAILABLE":
                values: dict[str, Any] = {"selector_rule": "lowest_exact_valid_candidate_cost_then_strategy_name", "eligible_candidate_costs": {name: value["purchase_cost"] for name, value in sorted(eligible.items())}}
                if outcome.outcome == "SELECTED": values["selected_purchase_cost"] = outcome.purchase_cost
                else:
                    selected_cost = eligible.get(selected, {}).get("purchase_cost")
                    values.update({"candidate_purchase_cost": outcome.purchase_cost, "selected_purchase_cost": selected_cost})
                    if code == "HIGHER_PURCHASE_COST_THAN_SELECTED" and isinstance(selected_cost, (int, float)) and outcome.purchase_cost is not None: values["purchase_cost_delta"] = outcome.purchase_cost - selected_cost
                result.append(_item(strategy, outcome, code, "SELECTION" if outcome.outcome == "SELECTED" else "NON_SELECTION", "STRATEGY_SELECTION_PROOF", values, "FULL", selected if outcome.outcome != "SELECTED" else None))
            else:
                result.append(_item(strategy, outcome, "SELECTION_REASON_UNAVAILABLE", "EVALUATION_UNAVAILABLE", "CANDIDATE_STATUS", {}, "UNAVAILABLE"))
        critic = candidate.get("critic") if isinstance(candidate.get("critic"), dict) else {}
        for finding in critic.get("findings", []) if isinstance(critic.get("findings"), list) else []:
            if not isinstance(finding, dict) or finding.get("severity") != "error" or not finding.get("code"): continue
            code = str(finding["code"]); technical = code in outcome.technical_failure_codes
            values = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
            result.append(_item(strategy, outcome, code, "TECHNICAL_EVALUATION_FAILURE" if technical else "BUSINESS_CONSTRAINT_FAILURE", "CRITIC_FINDING", values, "FULL" if values else "CODE_ONLY"))
        for code in critic.get("warnings", []) if isinstance(critic.get("warnings"), list) else []:
            if not isinstance(code, str): continue
            kind: Kind = "LIMITATION" if code in _LIMITATIONS or code.startswith("UNWEIGHTED_") else "WARNING"
            provenance = "STRESS" if code.startswith("STRESS_") else None
            result.append(_item(strategy, outcome, code, kind, "CRITIC_WARNING", {}, "CODE_ONLY", provenance=provenance))
    return sorted({item.evidence_id: item for item in result}.values(), key=lambda item: (item.strategy, item.reason_kind, item.reason_code, item.evidence_id))


def _item(strategy, outcome: StrategyCandidateOutcome, code, kind: Kind, source, values, availability, related=None, provenance=None):
    material = f"{strategy}|{outcome.outcome}|{kind}|{code}|{related or ''}|{sorted(values.items())}"
    return StrategyReasonEvidence(f"strategy-reason:{sha256(material.encode()).hexdigest()[:16]}", strategy, outcome.outcome, code, kind, source, dict(values), availability, related, provenance)
