"""Manager-safe projection of internal strategy outcome evidence."""
from app.decision_intelligence.strategy_comparison import strategy_label
from app.decision_intelligence.strategy_outcomes import project_strategy_outcomes
from app.decision_intelligence.strategy_reason_evidence import project_strategy_reason_evidence
from app.decision_intelligence.contracts import BriefStrategyEvaluation, BriefStrategyReason

_ORDER={"lean":0,"balanced":1,"protected":2}
_KINDS={"SELECTION":"selection","NON_SELECTION":"non_selection","BUSINESS_CONSTRAINT_FAILURE":"constraint_failure","EVALUATION_UNAVAILABLE":"evaluation_unavailable"}
_SAFE={"planned_cost","budget_limit","observed_fill_rate","required_fill_rate","candidate_purchase_cost","selected_purchase_cost","purchase_cost_delta","required_date","earliest_arrival_date","projected_quantity","capacity_limit","excess_quantity"}
def _code(code):
    if code.startswith("M4_") or code in {"CANDIDATE_MODEL_MISMATCH","STRESS_ACCOUNTING_INVALID"}: return "EVALUATION_TECHNICAL_FAILURE"
    return code.split(":",1)[0]
def project_strategy_evaluations(package):
    outcomes=project_strategy_outcomes(package)
    evidence={}
    for item in project_strategy_reason_evidence(package): evidence.setdefault(item.strategy,[]).append(item)
    rows=[]
    for outcome in outcomes:
        selected=outcome.outcome=="SELECTED"; status=outcome.outcome.lower()
        public=[]
        for item in evidence.get(outcome.strategy,[]):
            if item.reason_kind not in _KINDS and item.reason_kind!="TECHNICAL_EVALUATION_FAILURE": continue
            if item.reason_kind=="TECHNICAL_EVALUATION_FAILURE": kind="technical_failure"; code="EVALUATION_TECHNICAL_FAILURE"
            else: kind=_KINDS[item.reason_kind]; code=_code(item.reason_code)
            values={k:v for k,v in item.values.items() if k in _SAFE and isinstance(v,(int,float,str))}
            public.append(BriefStrategyReason(kind=kind,code=code,values=values))
        availability=[x.availability for x in evidence.get(outcome.strategy,[])]
        reason_status="unavailable" if outcome.selector_proof_status=="UNAVAILABLE" and selected else ("verified" if any(x=="FULL" for x in availability) else "code_only" if availability else "partial")
        rows.append(BriefStrategyEvaluation(strategy=outcome.strategy,label=strategy_label(outcome.strategy),status=status,selected=selected,feasible=(outcome.is_feasible if outcome.outcome not in {"TECHNICAL_FAILURE","NOT_EVALUATED"} else None),purchase_cost=outcome.purchase_cost,reason_status=reason_status,reasons=sorted(public,key=lambda x:(x.kind,x.code))))
    return sorted(rows,key=lambda x:(_ORDER.get(x.strategy,99),x.strategy))
