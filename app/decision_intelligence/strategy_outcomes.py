"""Read-only deterministic outcome projection for persisted Decision Run candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Outcome = Literal["SELECTED", "FEASIBLE_NOT_SELECTED", "REJECTED", "TECHNICAL_FAILURE", "NOT_EVALUATED"]
_TECHNICAL = {"M4_SIMULATION_FAILED", "M4_ACCOUNTING_INVALID", "CANDIDATE_MODEL_MISMATCH", "STRESS_ACCOUNTING_INVALID"}
_RULE = "lowest_exact_valid_candidate_cost_then_strategy_name"


@dataclass(frozen=True)
class StrategyCandidateOutcome:
    strategy: str
    outcome: Outcome
    selected: bool
    is_feasible: bool
    purchase_cost: float | None
    reason_codes: tuple[str, ...]
    hard_violation_codes: tuple[str, ...]
    technical_failure_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]
    selector_proof_status: Literal["VERIFIED", "UNAVAILABLE"]


def project_strategy_outcomes(package: dict[str, Any]) -> list[StrategyCandidateOutcome]:
    """Classify existing persisted candidate records without recomputing planning."""
    raw = package.get("strategies") if isinstance(package.get("strategies"), dict) else {}
    selected = package.get("recommended_strategy")
    selected = str(selected).lower() if selected else None
    candidates = {str(key).lower(): value for key, value in raw.items() if isinstance(value, dict)}
    proof_verified = _selection_proof_verified(package, candidates, selected)
    result = []
    for name in sorted(candidates):
        candidate = candidates[name]
        critic = candidate.get("critic") if isinstance(candidate.get("critic"), dict) else {}
        findings = critic.get("findings") if isinstance(critic.get("findings"), list) else []
        hard = tuple(sorted({str(x.get("code")) for x in findings if isinstance(x, dict) and x.get("severity") == "error" and x.get("code")}))
        warnings = tuple(sorted({str(x) for x in critic.get("warnings", []) if isinstance(x, str)}))
        feasible = bool(candidate.get("is_feasible"))
        technical = tuple(code for code in hard if code in _TECHNICAL or code.startswith("SOLVER_STATUS:ERROR") or code.startswith("SOLVER_STATUS:FAILED"))
        cost = candidate.get("purchase_cost")
        cost = float(cost) if isinstance(cost, (int, float)) else None
        if technical:
            outcome: Outcome = "TECHNICAL_FAILURE"; reasons = technical
        elif not feasible and hard:
            outcome = "REJECTED"; reasons = hard
        elif feasible and selected == name:
            outcome = "SELECTED"; reasons = (_selected_reason(candidates, selected, proof_verified),) if proof_verified else ("SELECTION_REASON_UNAVAILABLE",)
        elif feasible:
            outcome = "FEASIBLE_NOT_SELECTED"; reasons = (_alternative_reason(candidates, name, selected, proof_verified),) if proof_verified else ("SELECTION_REASON_UNAVAILABLE",)
        else:
            outcome = "NOT_EVALUATED"; reasons = ("EVALUATION_OUTCOME_UNAVAILABLE",)
        result.append(StrategyCandidateOutcome(name, outcome, outcome == "SELECTED", feasible, cost, reasons, hard, technical, warnings, "VERIFIED" if proof_verified else "UNAVAILABLE"))
    return result


def _selection_proof_verified(package: dict[str, Any], candidates: dict[str, dict], selected: str | None) -> bool:
    selection = package.get("strategy_selection") if isinstance(package.get("strategy_selection"), dict) else {}
    eligible = sorted(name for name, value in candidates.items() if value.get("is_feasible") and isinstance(value.get("purchase_cost"), (int, float)))
    if not selected or selection.get("rule") != _RULE or selection.get("selected_strategy") != selected or sorted(selection.get("eligible_candidates", [])) != eligible or selected not in eligible:
        return False
    winner = min(eligible, key=lambda name: (float(candidates[name]["purchase_cost"]), name))
    return winner == selected


def _selected_reason(candidates: dict[str, dict], selected: str | None, verified: bool) -> str:
    assert selected and verified
    cost = float(candidates[selected]["purchase_cost"])
    return "STRATEGY_NAME_TIEBREAK" if any(name != selected and value.get("is_feasible") and value.get("purchase_cost") == cost for name, value in candidates.items()) else "LOWEST_EXACT_VALID_CANDIDATE_COST"


def _alternative_reason(candidates: dict[str, dict], name: str, selected: str | None, verified: bool) -> str:
    assert selected and verified
    return "STRATEGY_NAME_TIEBREAK" if float(candidates[name]["purchase_cost"]) == float(candidates[selected]["purchase_cost"]) else "HIGHER_PURCHASE_COST_THAN_SELECTED"
