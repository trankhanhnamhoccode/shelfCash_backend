import json
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select

from app.core.exceptions import PlanningError
from app.core.provenance import canonical_hash
from app.models.operations import ForecastPredictionModel,ForecastRunModel
from app.models.planning import (IngredientDemandPredictionModel,IngredientDemandRunModel,
    ProcurementPlanLineModel,ProcurementPlanModel,ProcurementPlanRunModel)
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.planning import PlanningRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.procurement_planning_service import ProcurementPlanningService
from app.services.recipe_bom_service import RecipeBomService

def now():return datetime.now(timezone.utc)
def dump(x):return json.dumps(x,ensure_ascii=False,default=str)

class DecisionPlanningService:
 def __init__(self,factory):self.factory=factory
 def _forecast(self,s,store,run_id):
  StoreRepository(s).get_required(store);run=s.get(ForecastRunModel,run_id)
  if not run or run.store_id!=store:raise PlanningError("FORECAST_RUN_NOT_FOUND","Không tìm thấy forecast run.",{"forecast_run_id":run_id},http_status=404)
  if run.status!="completed":raise PlanningError("FORECAST_RUN_NOT_COMPLETED","Forecast run chưa completed.",{"status":run.status},http_status=409)
  predictions=list(s.scalars(select(ForecastPredictionModel).where(ForecastPredictionModel.forecast_run_id==run_id)))
  if not predictions:raise PlanningError("FORECAST_PREDICTIONS_MISSING","Forecast completed nhưng thiếu predictions.",http_status=500)
  return run,predictions
 def generate_demand(self,store,run_id,key=None):
  endpoint=f"/api/v1/stores/{store}/forecast-runs/{run_id}/ingredient-demand";request_hash=canonical_hash({"forecast_run_id":run_id})
  with self.factory() as s:
   run,predictions=self._forecast(s,store,run_id);repo=PlanningRepository(s);existing=repo.demand_run_for_forecast(run_id)
   if key:
    replay=IdempotencyService(IdempotencyRepository(s)).register(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:s.rollback();return self.get_demand(store,run_id)
   if existing and existing.status=="completed":return self.get_demand(store,run_id)
   demand_run=existing or IngredientDemandRunModel(ingredient_demand_run_id=str(uuid4()),forecast_run_id=run_id,store_id=store,status="running",warnings_json="[]",created_at=now())
   if not existing:s.add(demand_run);s.flush()
   try:
    scope=json.loads(run.scope_json or "{}").get("ingredient_ids",[])
    rows=RecipeBomService(s).expand(store,predictions,scope)
    warnings=sorted({w for row in rows for w in row["warnings"]})
    if scope and not rows:warnings.append("INGREDIENT_SCOPE_NO_MATCH")
    for row in rows:s.add(IngredientDemandPredictionModel(ingredient_demand_prediction_id=str(uuid4()),ingredient_demand_run_id=demand_run.ingredient_demand_run_id,
     forecast_run_id=run_id,store_id=store,ingredient_id=row["ingredient_id"],ingredient_name=row["ingredient_name"],target_date=row["target_date"],horizon=row["horizon"],unit=row["unit"],
     p25=row["p25"],p50=row["p50"],p75=row["p75"],source_product_count=row["source_product_count"],contributions_json=dump(row["contributions"]),warnings_json=dump(row["warnings"]),created_at=now()))
    demand_run.status="completed";demand_run.completed_at=now();demand_run.warnings_json=dump(warnings)
    AuditService(AuditLogRepository(s)).record(store_id=store,action="ingredient_demand_generated",resource_type="ingredient_demand_run",resource_id=demand_run.ingredient_demand_run_id,after={"forecast_run_id":run_id,"prediction_count":len(rows)},source="planning_service")
    if key:
     rec=IdempotencyRepository(s).get(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key);rec.resource_type="ingredient_demand_run";rec.resource_id=demand_run.ingredient_demand_run_id;rec.response_status=200
    s.commit()
   except PlanningError as exc:
    demand_run.status="blocked";demand_run.failure_code=exc.code;demand_run.failure_message=exc.message;demand_run.completed_at=now();s.commit();raise
  return self.get_demand(store,run_id)
 def get_demand(self,store,run_id):
  with self.factory() as s:
   self._forecast(s,store,run_id);repo=PlanningRepository(s);run=repo.demand_run_for_forecast(run_id)
   if not run:raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Ingredient demand chưa được tạo.",http_status=404)
   rows=repo.demand_predictions(run.ingredient_demand_run_id);warnings=json.loads(run.warnings_json or "[]")
   if run.status=="completed" and not rows and "INGREDIENT_SCOPE_NO_MATCH" not in warnings:raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Completed ingredient demand thiếu predictions.",http_status=500)
   return {"ingredient_demand_run_id":run.ingredient_demand_run_id,"forecast_run_id":run_id,"store_id":store,"status":run.status,"warnings":warnings,"failure_code":run.failure_code,"failure_message":run.failure_message,"created_at":run.created_at,"completed_at":run.completed_at,
    "predictions":[{"ingredient_id":x.ingredient_id,"ingredient_name":x.ingredient_name,"target_date":x.target_date,"horizon":x.horizon,"unit":x.unit,"p25":float(x.p25),"p50":float(x.p50),"p75":float(x.p75),"source_product_count":x.source_product_count,"contributions":json.loads(x.contributions_json),"warnings":json.loads(x.warnings_json)} for x in rows]}
 def generate_plans(self,store,run_id,body,key=None):
  endpoint=f"/api/v1/stores/{store}/forecast-runs/{run_id}/procurement-plans";payload=body.model_dump(mode="json");request_hash=canonical_hash(payload)
  if len(body.strategies)!=len(set(body.strategies)):raise PlanningError("FORECAST_INPUT_INVALID","strategies bị trùng.")
  if not body.use_latest_inventory:raise PlanningError("FORECAST_INPUT_INVALID","Backend chỉ hỗ trợ persisted latest inventory; what-if inventory chưa được hỗ trợ.")
  with self.factory() as s:
   forecast,_=self._forecast(s,store,run_id);repo=PlanningRepository(s);demand_run=repo.demand_run_for_forecast(run_id)
   if not demand_run or demand_run.status!="completed":raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Ingredient demand chưa completed.",http_status=409)
   demands=repo.demand_predictions(demand_run.ingredient_demand_run_id)
   if not demands:
    if "INGREDIENT_SCOPE_NO_MATCH" in json.loads(demand_run.warnings_json or "[]"):raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Ingredient scope không có demand để lập plan.")
    raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Thiếu ingredient demand.",http_status=500)
   if key:
    replay=IdempotencyService(IdempotencyRepository(s)).register(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:
     rid=replay.record.resource_id;s.rollback();return self.get_plans(store,run_id,rid)
   planning_run=ProcurementPlanRunModel(procurement_plan_run_id=str(uuid4()),forecast_run_id=run_id,ingredient_demand_run_id=demand_run.ingredient_demand_run_id,store_id=store,status="running",request_json=dump(payload),warnings_json="[]",created_at=now());s.add(planning_run);s.flush()
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key);rec.resource_type="procurement_plan_run";rec.resource_id=planning_run.procurement_plan_run_id
   s.commit()
   try:plans,recommended=ProcurementPlanningService(s).build(store,forecast,demands,body.strategies,body.use_open_purchase_orders,body.budget_override)
   except PlanningError as exc:
    failed=s.get(ProcurementPlanRunModel,planning_run.procurement_plan_run_id);failed.status="blocked";failed.failure_code=exc.code;failed.failure_message=exc.message;failed.completed_at=now();s.commit();raise
   except Exception as exc:
    failed=s.get(ProcurementPlanRunModel,planning_run.procurement_plan_run_id);failed.status="failed";failed.failure_code="PROCUREMENT_PLAN_INFEASIBLE";failed.failure_message=str(exc)[:500];failed.completed_at=now();s.commit()
    raise PlanningError("PROCUREMENT_PLAN_INFEASIBLE","Procurement planning thất bại.",http_status=500) from exc
   all_warnings=sorted(set(json.loads(demand_run.warnings_json or "[]")+[w for p in plans for w in p["warnings"]]))
   for plan_data in plans:
    plan=ProcurementPlanModel(procurement_plan_id=str(uuid4()),procurement_plan_run_id=planning_run.procurement_plan_run_id,strategy=plan_data["strategy"],is_feasible=plan_data["is_feasible"],is_recommended=plan_data["is_recommended"],total_purchase_cost=plan_data["total_purchase_cost"],projected_shortage_quantity=plan_data["projected_shortage_quantity"],projected_waste_quantity=plan_data["projected_waste_quantity"],fill_rate=plan_data["fill_rate"],budget_used=plan_data["budget_used"],metrics_json=dump({k:v for k,v in plan_data.items() if k not in {"lines","daily_projections","warnings"}}),daily_projections_json=dump(plan_data["daily_projections"]),warnings_json=dump(plan_data["warnings"]),created_at=now());s.add(plan);s.flush()
    for line in plan_data["lines"]:s.add(ProcurementPlanLineModel(procurement_plan_line_id=str(uuid4()),procurement_plan_id=plan.procurement_plan_id,ingredient_id=line["ingredient_id"],supplier_id=line["supplier_id"],supplier_term_id=line["supplier_term_id"],order_date=line["order_date"],expected_arrival_date=line["expected_arrival_date"],raw_required_quantity=line["raw_required_quantity"],order_quantity=line["order_quantity"],unit=line["unit"],pack_count=line["pack_count"],unit_cost=line["unit_cost"],line_cost=line["line_cost"],moq=line["moq"],pack_size=line["pack_size"],lead_time_days=line["lead_time_days"],reason_codes_json=dump(line["reason_codes"]),warnings_json=dump(line["warnings"]),created_at=now()))
   planning_run.status="completed";planning_run.completed_at=now();planning_run.recommended_strategy=recommended;planning_run.warnings_json=dump(all_warnings)
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key);rec.resource_type="procurement_plan_run";rec.resource_id=planning_run.procurement_plan_run_id;rec.response_status=200
   AuditService(AuditLogRepository(s)).record(store_id=store,action="procurement_plans_generated",resource_type="procurement_plan_run",resource_id=planning_run.procurement_plan_run_id,after={"forecast_run_id":run_id,"recommended_strategy":recommended},source="planning_service");s.commit();rid=planning_run.procurement_plan_run_id
  return self.get_plans(store,run_id,rid)
 def get_plans(self,store,run_id,planning_run_id=None):
  with self.factory() as s:
   self._forecast(s,store,run_id);repo=PlanningRepository(s);run=repo.plan_run(planning_run_id) if planning_run_id else repo.latest_plan_run(run_id)
   if not run or run.store_id!=store or run.forecast_run_id!=run_id:raise PlanningError("PLANNING_RUN_NOT_FOUND","Không tìm thấy planning run.",http_status=404)
   plans=repo.plans(run.procurement_plan_run_id)
   if run.status=="completed" and not plans:raise PlanningError("PLANNING_PERSISTENCE_INCONSISTENCY","Completed planning run thiếu plans.",http_status=500)
   out=[]
   for p in plans:
    metrics=json.loads(p.metrics_json);out.append({"procurement_plan_id":p.procurement_plan_id,"strategy":p.strategy,"is_feasible":p.is_feasible,"is_recommended":p.is_recommended,"total_purchase_cost":p.total_purchase_cost,"projected_shortage_quantity":float(p.projected_shortage_quantity),"projected_waste_quantity":float(p.projected_waste_quantity),"fill_rate":float(p.fill_rate),"budget_used":p.budget_used,"metrics":metrics,"warnings":json.loads(p.warnings_json),"daily_projections":json.loads(p.daily_projections_json),"lines":[{"ingredient_id":x.ingredient_id,"supplier_id":x.supplier_id,"supplier_term_id":x.supplier_term_id,"order_date":x.order_date,"expected_arrival_date":x.expected_arrival_date,"raw_required_quantity":float(x.raw_required_quantity),"order_quantity":float(x.order_quantity),"rounding_excess":float(x.order_quantity-x.raw_required_quantity),"unit":x.unit,"pack_count":x.pack_count,"unit_cost":x.unit_cost,"line_cost":x.line_cost,"moq":float(x.moq) if x.moq is not None else None,"pack_size":float(x.pack_size) if x.pack_size is not None else None,"lead_time_days":x.lead_time_days,"reason_codes":json.loads(x.reason_codes_json),"warnings":json.loads(x.warnings_json)} for x in repo.lines(p.procurement_plan_id)]})
   return {"procurement_plan_run_id":run.procurement_plan_run_id,"forecast_run_id":run_id,"ingredient_demand_run_id":run.ingredient_demand_run_id,"store_id":store,"status":run.status,"recommended_strategy":run.recommended_strategy,"warnings":json.loads(run.warnings_json or "[]"),"failure_code":run.failure_code,"failure_message":run.failure_message,"created_at":run.created_at,"completed_at":run.completed_at,"plans":out}
