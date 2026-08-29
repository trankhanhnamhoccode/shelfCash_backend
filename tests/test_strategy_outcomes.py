from app.decision_intelligence.strategy_outcomes import project_strategy_outcomes


def _candidate(feasible, cost, findings=(), warnings=()):
    return {"is_feasible": feasible, "purchase_cost": cost, "critic": {"findings": [{"code": code, "severity": "error"} for code in findings], "warnings": list(warnings)}}


def _package(strategies, selected="protected", proof=True):
    value = {"strategies": strategies, "recommended_strategy": selected}
    if proof:
        value["strategy_selection"] = {"rule": "lowest_exact_valid_candidate_cost_then_strategy_name", "selected_strategy": selected, "eligible_candidates": sorted(k for k, v in strategies.items() if v["is_feasible"] and v["purchase_cost"] is not None)}
    return value


def test_outcomes_cover_selection_business_technical_warnings_and_order_independence():
    strategies = {"protected": _candidate(True, 80, warnings=["STRESS_SHORTAGE_OBSERVED", "CAPACITY_NOT_EVALUATED"]), "lean": _candidate(False, 70, ["BUDGET", "SERVICE_LEVEL_REQUIREMENT"]), "balanced": _candidate(True, 100, warnings=["RISK_METRIC_NOT_AVAILABLE"])}
    outcomes = {x.strategy: x for x in project_strategy_outcomes(_package(strategies))}
    assert outcomes["protected"].outcome == "SELECTED" and outcomes["protected"].reason_codes == ("LOWEST_EXACT_VALID_CANDIDATE_COST",)
    assert outcomes["balanced"].outcome == "FEASIBLE_NOT_SELECTED" and outcomes["balanced"].reason_codes == ("HIGHER_PURCHASE_COST_THAN_SELECTED",)
    assert outcomes["lean"].outcome == "REJECTED" and outcomes["lean"].hard_violation_codes == ("BUDGET", "SERVICE_LEVEL_REQUIREMENT")
    assert outcomes["protected"].warning_codes == ("CAPACITY_NOT_EVALUATED", "STRESS_SHORTAGE_OBSERVED")
    assert outcomes == {x.strategy: x for x in project_strategy_outcomes(_package(dict(reversed(list(strategies.items())))))}


def test_tie_technical_no_feasible_and_unavailable_proof():
    tied = _package({"balanced": _candidate(True, 80), "protected": _candidate(True, 80)}, selected="balanced")
    outcomes = {x.strategy: x for x in project_strategy_outcomes(tied)}
    assert outcomes["balanced"].reason_codes == ("STRATEGY_NAME_TIEBREAK",) and outcomes["protected"].reason_codes == ("STRATEGY_NAME_TIEBREAK",)
    technical = project_strategy_outcomes(_package({"lean": _candidate(False, 0, ["M4_SIMULATION_FAILED"]), "balanced": _candidate(False, 0, ["BUDGET"])}, selected=None))
    assert [x.outcome for x in technical] == ["REJECTED", "TECHNICAL_FAILURE"]
    unavailable = project_strategy_outcomes(_package({"lean": _candidate(True, 90), "balanced": _candidate(True, 80)}, selected="balanced", proof=False))
    assert unavailable[1].reason_codes == ("SELECTION_REASON_UNAVAILABLE",)
