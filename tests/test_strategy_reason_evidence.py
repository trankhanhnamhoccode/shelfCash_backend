from app.decision_intelligence.strategy_reason_evidence import project_strategy_reason_evidence


def _candidate(feasible, cost, findings=(), warnings=()):
    return {"is_feasible": feasible, "purchase_cost": cost, "critic": {"findings": list(findings), "warnings": list(warnings)}}


def _package(proof=True):
    strategies = {
        "lean": _candidate(False, 5400000, [{"code":"BUDGET","severity":"error","evidence":{"planned_cost":5400000,"budget_limit":5000000}}, {"code":"SERVICE_LEVEL_REQUIREMENT","severity":"error","evidence":{"observed_fill_rate":.6,"required_fill_rate":.8}}]),
        "balanced": _candidate(True, 5000000),
        "protected": _candidate(True, 4676000, warnings=["STRESS_SHORTAGE_OBSERVED", "CAPACITY_NOT_EVALUATED", "RISK_METRIC_NOT_AVAILABLE"]),
    }
    p={"recommended_strategy":"protected","strategies":strategies}
    if proof:p["strategy_selection"]={"rule":"lowest_exact_valid_candidate_cost_then_strategy_name","selected_strategy":"protected","eligible_candidates":["balanced","protected"]}
    return p


def test_selection_rejection_values_warnings_and_stability():
    evidence=project_strategy_reason_evidence(_package())
    by={(x.strategy,x.reason_code):x for x in evidence}
    assert by[("protected","LOWEST_EXACT_VALID_CANDIDATE_COST")].values["selected_purchase_cost"] == 4676000
    assert by[("balanced","HIGHER_PURCHASE_COST_THAN_SELECTED")].values["purchase_cost_delta"] == 324000
    assert by[("lean","BUDGET")].values == {"planned_cost":5400000,"budget_limit":5000000}
    assert by[("lean","SERVICE_LEVEL_REQUIREMENT")].values["required_fill_rate"] == .8
    assert by[("protected","CAPACITY_NOT_EVALUATED")].reason_kind == "LIMITATION"
    assert by[("protected","STRESS_SHORTAGE_OBSERVED")].provenance == "STRESS"
    assert evidence == project_strategy_reason_evidence(_package())


def test_proof_unavailable_tie_technical_and_code_only():
    unavailable=project_strategy_reason_evidence(_package(False))
    selected=next(x for x in unavailable if x.strategy=="protected" and x.reason_kind=="EVALUATION_UNAVAILABLE")
    assert selected.reason_code == "SELECTION_REASON_UNAVAILABLE"
    p=_package(); p["strategies"]["balanced"]["is_feasible"]=False; p["strategies"]["balanced"]["critic"]["findings"]=[{"code":"M4_SIMULATION_FAILED","severity":"error"},{"code":"BUDGET","severity":"error"}]
    items=project_strategy_reason_evidence(p)
    assert {(x.reason_code,x.reason_kind,x.availability) for x in items if x.strategy=="balanced"} >= {("M4_SIMULATION_FAILED","TECHNICAL_EVALUATION_FAILURE","CODE_ONLY"),("BUDGET","BUSINESS_CONSTRAINT_FAILURE","CODE_ONLY")}
