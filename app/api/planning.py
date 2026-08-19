from fastapi import APIRouter,Depends,Header
from app.dependencies import get_decision_planning_service,require_api_key
from app.schemas.planning import ProcurementPlansRequest
from app.schemas.decision import DecisionRunRequest, ExplanationRequest, WhatIfRequest
from app.decision_intelligence.contracts import DecisionBriefFacts, DecisionExplanationResponse, WhatIfResponse

router=APIRouter(tags=["planning"],dependencies=[Depends(require_api_key)])
def key(value:str|None=Header(None,alias="Idempotency-Key")):return value
@router.post("/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand")
def generate_demand(store_id:str,forecast_run_id:str,k=Depends(key),service=Depends(get_decision_planning_service)):return service.generate_demand(store_id,forecast_run_id,k)
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand")
def read_demand(store_id:str,forecast_run_id:str,service=Depends(get_decision_planning_service)):return service.get_demand(store_id,forecast_run_id)
@router.post("/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans")
def generate_plans(store_id:str,forecast_run_id:str,body:ProcurementPlansRequest,k=Depends(key),service=Depends(get_decision_planning_service)):return service.generate_plans(store_id,forecast_run_id,body,k)
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans")
def read_plans(store_id:str,forecast_run_id:str,procurement_plan_run_id:str|None=None,service=Depends(get_decision_planning_service)):return service.get_plans(store_id,forecast_run_id,procurement_plan_run_id)
@router.post("/stores/{store_id}/decision-runs")
def create_decision(store_id:str,body:DecisionRunRequest,k=Depends(key),service=Depends(get_decision_planning_service)):return service.generate_decision(store_id,body,k)
@router.get("/decision-runs/{decision_run_id}")
def read_decision(decision_run_id:str,service=Depends(get_decision_planning_service)):return service.get_decision(decision_run_id)
@router.get("/decision-runs/{decision_run_id}/brief",response_model=DecisionBriefFacts)
def read_decision_brief(decision_run_id:str,service=Depends(get_decision_planning_service)):return service.get_decision_brief(decision_run_id)
@router.post("/decision-runs/{decision_run_id}/explanation",response_model=DecisionExplanationResponse)
def explain_decision(decision_run_id:str,body:ExplanationRequest,service=Depends(get_decision_planning_service)):return service.explain_decision(decision_run_id,body)
@router.post("/decision-runs/{decision_run_id}/what-if",response_model=WhatIfResponse)
def what_if_decision(decision_run_id:str,body:WhatIfRequest,service=Depends(get_decision_planning_service)):return service.what_if_decision(decision_run_id,body)
