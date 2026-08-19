import json
import logging
import time
from types import SimpleNamespace
from datetime import datetime,timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import delete, select

from app.core.exceptions import PlanningError
from app.core.provenance import canonical_hash
from app.models.operations import ForecastPredictionModel,ForecastRunModel,PlanRunModel,RecommendationModel
from app.models.business import IngredientModel,SupplierModel
from app.models.planning import (IngredientDemandPredictionModel,IngredientDemandRunModel,
    ProcurementPlanLineModel,ProcurementPlanModel,ProcurementPlanRunModel)
from app.models.decision import DecisionRunModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.planning import PlanningRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.services.procurement_planning_service import ProcurementPlanningService
from app.services.decision.adapters.bom_adapter import CoreBomAdapter
from app.services.decision.adapters.procurement_adapter import CoreProcurementAdapter
from app.schemas.planning import ProcurementPlansRequest

def now():return datetime.now(timezone.utc)
def dump(x):return json.dumps(x,ensure_ascii=False,default=str)
logger=logging.getLogger("shelfcash.planning")
LEGACY_STRATEGIES={"economy":"lean","balanced":"balanced","safe":"protected","lean":"lean","protected":"protected"}

class DecisionPlanningService:
 def __init__(self,factory,settings=None,llm_provider=None):self.factory=factory;self.settings=settings;self.llm_provider=llm_provider
 def _forecast(self,s,store,run_id):
  StoreRepository(s).get_required(store);run=s.get(ForecastRunModel,run_id)
  if not run or run.store_id!=store:raise PlanningError("FORECAST_RUN_NOT_FOUND","Không tìm thấy forecast run.",{"forecast_run_id":run_id},http_status=404)
  if run.status!="completed":raise PlanningError("FORECAST_RUN_NOT_COMPLETED","Forecast run chưa completed.",{"status":run.status},http_status=409)
  predictions=list(s.scalars(select(ForecastPredictionModel).where(ForecastPredictionModel.forecast_run_id==run_id)))
  if not predictions:raise PlanningError("FORECAST_PREDICTIONS_MISSING","Forecast completed nhưng thiếu predictions.",http_status=500)
  return run,predictions
 def generate_demand(self,store,run_id,key=None,refresh=False):
  endpoint=f"/api/v1/stores/{store}/forecast-runs/{run_id}/ingredient-demand";request_hash=canonical_hash({"forecast_run_id":run_id})
  with self.factory() as s:
   run,predictions=self._forecast(s,store,run_id);repo=PlanningRepository(s);existing=repo.demand_run_for_forecast(run_id)
   if key:
    replay=IdempotencyService(IdempotencyRepository(s)).register(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:s.rollback();return self.get_demand(store,run_id)
   if existing and existing.status=="completed" and not refresh:return self.get_demand(store,run_id)
   demand_run=existing or IngredientDemandRunModel(ingredient_demand_run_id=str(uuid4()),forecast_run_id=run_id,store_id=store,status="running",warnings_json="[]",created_at=now())
   if not existing:s.add(demand_run);s.flush()
   if existing and refresh:
    s.execute(delete(IngredientDemandPredictionModel).where(IngredientDemandPredictionModel.ingredient_demand_run_id==demand_run.ingredient_demand_run_id))
    demand_run.status="running";demand_run.failure_code=None;demand_run.failure_message=None;demand_run.completed_at=None
   try:
    scope=json.loads(run.scope_json or "{}").get("ingredient_ids",[])
    rows=CoreBomAdapter(s).expand(store,run,predictions,scope)
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
   strategy_source="explicit" if "strategies" in body.model_fields_set else "request_default"
   try:plans,recommended=ProcurementPlanningService(s).build(store,forecast,demands,body.strategies,body.use_open_purchase_orders,body.budget_override,strategy_source)
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

 def create_legacy_plan(self,store,body,key=None,request_id=None):
  started=time.monotonic();strategy=LEGACY_STRATEGIES.get(body.strategy)
  if strategy is None:raise PlanningError("FORECAST_INPUT_INVALID","strategy không hợp lệ.",{"allowed":sorted(LEGACY_STRATEGIES)})
  endpoint=f"/api/v1/stores/{store}/plan-runs";payload=body.model_dump(mode="json");request_hash=canonical_hash(payload)
  logger.info("legacy_plan_adapter_started request_id=%s store_id=%s forecast_run_id=%s strategy=%s budget_limit=%s",request_id,store,body.forecast_run_id,strategy,body.budget_limit)
  with self.factory() as s:
   forecast,_=self._forecast(s,store,body.forecast_run_id)
   if body.as_of_date!=forecast.cutoff_date:raise PlanningError("FORECAST_INPUT_INVALID","as_of_date phải bằng forecast cutoff_date.",{"as_of_date":body.as_of_date,"cutoff_date":forecast.cutoff_date})
   if key:
    replay=IdempotencyService(IdempotencyRepository(s)).register(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:
     rid=replay.record.resource_id;s.rollback();return self.get_legacy_plan_metadata(store,rid)
   rid=str(uuid4());legacy=PlanRunModel(plan_run_id=rid,store_id=store,forecast_run_id=body.forecast_run_id,strategy=body.strategy,
    budget_limit=body.budget_limit,as_of_date=body.as_of_date,include_open_purchase_orders=body.include_open_purchase_orders,
    status="running",engine_status="decision_planning",request_hash=request_hash,input_snapshot_json=dump({"request":payload,"planning_strategy":strategy}),warnings_json="[]",created_at=now())
   s.add(legacy)
   if key:
    record=IdempotencyRepository(s).get(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key);record.resource_type="plan_run";record.resource_id=rid;record.response_status=200
   s.commit()
  try:
   logger.info("planning_started request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s",request_id,store,body.forecast_run_id,rid,strategy)
   with self.factory() as s:demand=PlanningRepository(s).demand_run_for_forecast(body.forecast_run_id)
   if not demand or demand.status!="completed":self.generate_demand(store,body.forecast_run_id)
   logger.info("ingredient_demand_resolved request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s",request_id,store,body.forecast_run_id,rid)
   result=self.generate_plans(store,body.forecast_run_id,ProcurementPlansRequest(strategies=[strategy],
    use_open_purchase_orders=body.include_open_purchase_orders,use_latest_inventory=True,budget_override=body.budget_limit))
   planning_run_id=result["procurement_plan_run_id"];selected=result["plans"][0]
   logger.info("inventory_simulation_completed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s",request_id,store,body.forecast_run_id,rid,planning_run_id,strategy)
   logger.info("procurement_plan_created request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s",request_id,store,body.forecast_run_id,rid,planning_run_id,strategy)
   with self.factory() as s:
    legacy=s.get(PlanRunModel,rid);legacy.procurement_plan_run_id=planning_run_id;legacy.status="completed";legacy.engine_status="decision_planning";legacy.completed_at=now();legacy.warnings_json=dump(result["warnings"])
    for line in selected["lines"]:
     if line["supplier_id"] and line["order_quantity"]>0:
      s.add(RecommendationModel(recommendation_id=str(uuid4()),plan_run_id=rid,store_id=store,ingredient_id=line["ingredient_id"],unit=line["unit"],order_quantity=Decimal(str(line["order_quantity"])),unit_cost=line["unit_cost"],cost=line["line_cost"],supplier_id=line["supplier_id"],moq=Decimal(str(line["moq"])),pack_size=Decimal(str(line["pack_size"])),lead_time_days=line["lead_time_days"],created_at=now()))
    AuditService(AuditLogRepository(s)).record(store_id=store,action="legacy_plan_generated",resource_type="plan_run",resource_id=rid,after={"forecast_run_id":body.forecast_run_id,"procurement_plan_run_id":planning_run_id,"strategy":strategy},source="planning_service");s.commit()
   logger.info("legacy_plan_adapter_completed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s planning_run_id=%s strategy=%s duration=%.3f",request_id,store,body.forecast_run_id,rid,planning_run_id,strategy,time.monotonic()-started)
   return self.get_legacy_plan_metadata(store,rid)
  except PlanningError as exc:
   with self.factory() as s:
    legacy=s.get(PlanRunModel,rid)
    if legacy:legacy.status="blocked";legacy.failure_code=exc.code;legacy.failure_message=exc.message;legacy.completed_at=now();s.commit()
   logger.exception("legacy_plan_adapter_failed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s failure_code=%s duration=%.3f",request_id,store,body.forecast_run_id,rid,strategy,exc.code,time.monotonic()-started)
   raise
  except Exception as exc:
   with self.factory() as s:
    legacy=s.get(PlanRunModel,rid)
    if legacy:legacy.status="failed";legacy.failure_code="PROCUREMENT_PLAN_INFEASIBLE";legacy.failure_message=str(exc)[:500];legacy.completed_at=now();s.commit()
   logger.exception("legacy_plan_adapter_failed request_id=%s store_id=%s forecast_run_id=%s plan_run_id=%s strategy=%s failure_code=PROCUREMENT_PLAN_INFEASIBLE duration=%.3f",request_id,store,body.forecast_run_id,rid,strategy,time.monotonic()-started)
   raise PlanningError("PROCUREMENT_PLAN_INFEASIBLE","Planning execution thất bại.",http_status=500) from exc

 def get_legacy_plan_metadata(self,store,rid):
  with self.factory() as s:
   StoreRepository(s).get_required(store);run=s.get(PlanRunModel,rid)
   if not run or run.store_id!=store:raise PlanningError("PLANNING_RUN_NOT_FOUND","Không tìm thấy plan run.",{"plan_run_id":rid},http_status=404)
   strategy=LEGACY_STRATEGIES.get(run.strategy,run.strategy)
   return {"plan_run_id":run.plan_run_id,"store_id":run.store_id,"forecast_run_id":run.forecast_run_id,"procurement_plan_run_id":run.procurement_plan_run_id,
    "status":run.status,"engine_status":run.engine_status,"strategy":run.strategy,"planning_strategy":strategy,"budget_limit":run.budget_limit,"as_of_date":run.as_of_date,
    "include_open_purchase_orders":run.include_open_purchase_orders,"created_at":run.created_at,"completed_at":run.completed_at,
    "result_url":f"/api/v1/stores/{store}/plan-runs/{rid}/result","warnings":json.loads(run.warnings_json or "[]"),"failure_code":run.failure_code,"failure_message":run.failure_message}

 def get_legacy_plan_result(self,store,rid):
  metadata=self.get_legacy_plan_metadata(store,rid)
  if metadata["status"]!="completed":
   raise PlanningError(metadata["failure_code"] or "PROCUREMENT_PLAN_INFEASIBLE",metadata["failure_message"] or "Plan run chưa completed.",{"plan_run_id":rid},http_status=409)
  result=self.get_plans(store,metadata["forecast_run_id"],metadata["procurement_plan_run_id"]);selected=next((p for p in result["plans"] if p["strategy"]==metadata["planning_strategy"]),None)
  if selected is None:raise PlanningError("PLANNING_PERSISTENCE_INCONSISTENCY","Không tìm thấy selected strategy trong planning result.",http_status=500)
  with self.factory() as s:
   recommendations={x.ingredient_id:x.recommendation_id for x in s.scalars(select(RecommendationModel).where(RecommendationModel.plan_run_id==rid))}
   lines=[]
   for line in selected["lines"]:
    ingredient=s.get(IngredientModel,line["ingredient_id"]);supplier=s.get(SupplierModel,line["supplier_id"]) if line["supplier_id"] else None
    lines.append({**line,"ingredient_name":ingredient.ingredient if ingredient else None,"supplier_name":supplier.supplier if supplier else None,"recommendation_id":recommendations.get(line["ingredient_id"])})
  metrics=selected["metrics"]
  return {**metadata,"is_feasible":selected["is_feasible"],"is_recommended":selected["is_recommended"],"total_purchase_cost":selected["total_purchase_cost"],
   "projected_shortage_quantity":selected["projected_shortage_quantity"],"projected_waste_quantity":selected["projected_waste_quantity"],"fill_rate":selected["fill_rate"],
   "budget_used":selected["budget_used"],"budget_remaining":metrics.get("budget_remaining"),"budget_trace":metrics.get("budget_trace",{}),"constraint_violations":metrics.get("constraint_violations",[]),
   "warnings":sorted(set(metadata["warnings"]+selected["warnings"])),"storage_capacity_trace":metrics.get("storage_capacity_trace",{}),
   "shelf_life_trace":metrics.get("shelf_life_trace",{}),
   "plan_lines":lines,"simulation_summary":selected["daily_projections"]}

 def generate_decision(self,store,body,key=None):
  """Run the core path and persist one self-contained Decision Package."""
  mode=body.engine_mode or getattr(self.settings,"decision_engine_mode","legacy")
  if mode=="legacy":
   raise PlanningError("DECISION_ENGINE_LEGACY", "Use existing procurement-plan endpoints while DECISION_ENGINE_MODE=legacy.",http_status=409)
  if body.as_of_date is None:raise PlanningError("FORECAST_INPUT_INVALID","as_of_date is required.")
  endpoint=f"/api/v1/stores/{store}/decision-runs";payload=body.model_dump(mode="json");request_hash=canonical_hash(payload)
  with self.factory() as s:
   forecast,_=self._forecast(s,store,body.forecast_run_id)
   if forecast.cutoff_date!=body.as_of_date:raise PlanningError("FORECAST_INPUT_INVALID","as_of_date must equal forecast cutoff_date.",{"as_of_date":body.as_of_date,"cutoff_date":forecast.cutoff_date})
   if body.horizon_days!=forecast.horizon_days:raise PlanningError("FORECAST_INPUT_INVALID","horizon_days must equal forecast horizon.",{"horizon_days":body.horizon_days,"forecast_horizon":forecast.horizon_days})
   if key:
    replay=IdempotencyService(IdempotencyRepository(s)).register(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:
     rid=replay.record.resource_id;s.rollback();return self.get_decision(rid)
  # Decision packages must use a BOM expansion generated by the currently
  # deployed core; a completed run can otherwise retain a historical derived
  # demand payload after a deterministic BOM fix.
  self.generate_demand(store,body.forecast_run_id,refresh=True)
  with self.factory() as s:
   forecast,predictions=self._forecast(s,store,body.forecast_run_id);demands=PlanningRepository(s).demand_predictions(PlanningRepository(s).demand_run_for_forecast(forecast.forecast_run_id).ingredient_demand_run_id)
   if not demands:raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","No ingredient demand is available for decision.",http_status=409)
   rid=str(uuid4());started=time.monotonic()
   logger.info("decision_stage_started decision_run_id=%s store_id=%s stage=core_runner engine_mode=%s",rid,store,mode)
   try:
    adapter=CoreProcurementAdapter(s);result,request,baseline,scenario_metadata=adapter.optimize(store,forecast,demands,body.budget_override,body.include_open_purchase_orders,
     predictions=predictions,engine_mode=mode,scenario_count=body.scenario_count or getattr(self.settings,"decision_scenario_count",100),seed=body.random_seed if body.random_seed is not None else getattr(self.settings,"decision_random_seed",42),scenario_method=getattr(self.settings,"decision_scenario_method","residual_bootstrap"))
   except Exception as exc:
    raise PlanningError("OPTIMIZATION_INFEASIBLE","Core decision execution failed.",{"reason":str(exc)[:500]},http_status=422) from exc
   forecast_trace={"decision_run_id":rid,"requested_forecast_run_id":body.forecast_run_id,"resolved_forecast_run_id":forecast.forecast_run_id,"forecast_store_id":forecast.store_id,"forecast_cutoff_date":forecast.cutoff_date.isoformat(),"forecast_target_min":min(item.target_date for item in predictions).isoformat(),"forecast_target_max":max(item.target_date for item in predictions).isoformat(),"prediction_count":len(predictions)}
   package=self._decision_package(rid,store,forecast,mode,result,request,baseline,demands,scenario_metadata,forecast_trace,adapter.shortage_economics)
   run=DecisionRunModel(decision_run_id=rid,store_id=store,forecast_run_id=forecast.forecast_run_id,as_of_date=forecast.cutoff_date,horizon_days=forecast.horizon_days,engine_mode=mode,status=package["status"],scenario_method=package["technical_metrics"]["scenario_method"],scenario_count=package["technical_metrics"]["scenario_count"],random_seed=request.seed,recommended_strategy=package["recommended_strategy"],request_json=dump(payload),package_json=dump(package),warnings_json=dump(package["warnings"]),created_at=now(),completed_at=now())
   s.add(run)
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=endpoint,http_method="POST",idempotency_key=key);rec.resource_type="decision_run";rec.resource_id=rid;rec.response_status=200
   s.commit()
   logger.info("decision_stage_completed decision_run_id=%s store_id=%s stage=core_runner duration_ms=%d scenario_count=%s optimizer_type=%s warning_count=%d",rid,store,int((time.monotonic()-started)*1000),package["technical_metrics"]["scenario_count"],package["technical_metrics"]["optimizer_type"],len(package["warnings"]))
  return package

 def get_decision(self,rid):
  with self.factory() as s:
   run=s.get(DecisionRunModel,rid)
   if not run:raise PlanningError("DECISION_RUN_NOT_FOUND","Decision run not found.",{"decision_run_id":rid},http_status=404)
   return json.loads(run.package_json)

 def get_decision_brief(self,rid):
  from app.decision_intelligence import DecisionBriefBuilder, ShelfCashDecisionIntelligenceAdapter
  with self.factory() as s:
   run=s.get(DecisionRunModel,rid)
   if not run:raise PlanningError("DECISION_RUN_NOT_FOUND","Decision run not found.",{"decision_run_id":rid},http_status=404)
   brief=DecisionBriefBuilder().build(s,run)
   # Evidence is derived from the same immutable package, never persisted or used by M1-M5.
   return brief.model_copy(update={"evidence":ShelfCashDecisionIntelligenceAdapter().evidence_briefs(brief)})

 def explain_decision(self,rid,body):
  """M6 read-only explanation with legacy deterministic fallback."""
  try:
   from app.decision_intelligence.narrative import DecisionNarrativeProvider
   brief=self.get_decision_brief(rid)
   return DecisionNarrativeProvider(self.llm_provider,self.settings).explain(brief,question=body.question,language=body.language,detail_level=body.detail_level).model_dump(mode="json")
  except Exception:
   logger.exception("decision_intelligence_failed decision_run_id=%s",rid)
   return self._template_explanation(rid,body)

 def _template_explanation(self,rid,body):
  """Legacy response retained exclusively as M6 failure fallback."""
  package=self.get_decision(rid); reasons=package.get("reason_codes",[]); messages=[]
  mapping={"PACK_SIZE_ROUNDING":"Số lượng đặt được làm tròn theo quy cách đóng gói của nhà cung cấp.",
           "MOQ_ROUNDING":"Số lượng đặt được nâng lên để đáp ứng mức đặt tối thiểu.",
           "STOCKOUT_RISK_HIGH":"Kế hoạch ưu tiên giảm nguy cơ thiếu hàng trong kỳ kế hoạch.",
           "SUPPLIER_DELAY_SENSITIVITY":"Độ trễ giao hàng làm kế hoạch nhạy cảm hơn với thiếu hàng."}
  for reason in reasons:
   if reason.get("code") in mapping: messages.append(mapping[reason["code"]])
  if not messages: messages.append("Kế hoạch dựa trên forecast, BOM, tồn kho theo lô và các ràng buộc nhà cung cấp hiện có.")
  summary=" ".join(messages)
  return {"source":"template","language":body.language,"detail_level":body.detail_level,"summary":summary,"why_this_plan":messages,"main_risks":package.get("warnings",[]),"tradeoffs":[],"important_assumptions":["Forecast is uncertain and does not guarantee demand."],"decision_run_id":rid,"answer":summary,"intent":"FALLBACK","entities":{"ingredient_ids":[],"supplier_ids":[]},"claims":[],"citations":[],"grounded":False,"provider":"legacy_template_fallback"}

 def what_if_decision(self,rid,body):
  """Execute an in-memory M1-M5 hypothetical; no ORM entity is mutated or persisted."""
  from app.decision_intelligence import DecisionBriefBuilder, ShelfCashDecisionIntelligenceAdapter
  with self.factory() as s:
   run=s.get(DecisionRunModel,rid)
   if not run:raise PlanningError("DECISION_RUN_NOT_FOUND","Decision run not found.",{"decision_run_id":rid},http_status=404)
   baseline_package=json.loads(run.package_json)
   baseline_brief=DecisionBriefBuilder().build(s,run)
   baseline_brief=baseline_brief.model_copy(update={"evidence":ShelfCashDecisionIntelligenceAdapter().evidence_briefs(baseline_brief),"data_availability":{**baseline_brief.data_availability,"authority":"BASELINE"}})
   forecast,predictions=self._forecast(s,run.store_id,run.forecast_run_id)
   multiplier=body.demand_multiplier if body.demand_multiplier is not None else 1.0
   supplier_delay_days=body.supplier_delay_days if body.supplier_delay_days is not None else 0
   demands=[]
   for item in baseline_package.get("ingredient_demand",[]):
    demands.append(SimpleNamespace(ingredient_id=str(item["ingredient_id"]),target_date=datetime.fromisoformat(str(item["target_date"])).date(),unit=item.get("unit"),p25=float(item.get("p25",0))*multiplier,p50=float(item.get("p50",0))*multiplier,p75=float(item.get("p75",0))*multiplier,contributions_json=dump(item.get("contributions",[]))))
   if not demands:raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Persisted decision package has no ingredient demand.",http_status=409)
   try:
    adapter=CoreProcurementAdapter(s);result,request,inventory_baseline,scenario_metadata=adapter.optimize(run.store_id,forecast,demands,body.budget_limit,True,predictions=predictions,engine_mode=run.engine_mode,scenario_count=run.scenario_count,seed=run.random_seed,scenario_method=getattr(self.settings,"decision_scenario_method","residual_bootstrap"),supplier_delay_days=supplier_delay_days)
   except Exception as exc:
    raise PlanningError("WHAT_IF_EXECUTION_FAILED","Hypothetical decision execution failed.",{"reason":str(exc)[:500]},http_status=422) from exc
   hypothetical_id=f"what-if:{rid}"
   trace={"decision_run_id":hypothetical_id,"baseline_decision_run_id":rid,"mutation_scope":"ingredient_demand","demand_multiplier":multiplier,"supplier_delay_days":supplier_delay_days,"budget_limit":body.budget_limit}
   package=self._decision_package(hypothetical_id,run.store_id,forecast,run.engine_mode,result,request,inventory_baseline,demands,scenario_metadata,trace,adapter.shortage_economics)
   if body.strategy is not None:self._select_hypothetical_strategy(package,body.strategy)
   hypothetical_run=SimpleNamespace(decision_run_id=hypothetical_id,store_id=run.store_id,forecast_run_id=run.forecast_run_id,as_of_date=run.as_of_date,horizon_days=run.horizon_days,status=package["status"],package_json=dump(package))
   hypothetical=DecisionBriefBuilder().build(s,hypothetical_run)
   hypothetical=hypothetical.model_copy(update={"evidence":ShelfCashDecisionIntelligenceAdapter().evidence_briefs(hypothetical),"data_availability":{**hypothetical.data_availability,"authority":"HYPOTHETICAL","mutation_scope":"ingredient_demand"}})
   comparison=self._what_if_comparison(baseline_brief,hypothetical)
   explanation=None
   try:
    answer=ShelfCashDecisionIntelligenceAdapter().explain(hypothetical,question=None,language="vi",detail_level="simple")
    explanation={"answer":answer.answer,"citations":[x.model_dump(mode="json") for x in answer.citations],"grounded":answer.grounded,"authority":"HYPOTHETICAL"}
   except Exception:logger.exception("what_if_explanation_failed decision_run_id=%s",rid)
   return {"decision_run_id":rid,"baseline":baseline_brief.model_dump(mode="json"),"hypothetical":hypothetical.model_dump(mode="json"),"mutations":body.model_dump(mode="json"),"comparison":comparison,"grounded_explanation":explanation,"generated_at":now()}

 @staticmethod
 def _select_hypothetical_strategy(package,strategy):
  candidate=package.get("strategies",{}).get(strategy,{})
  if candidate.get("is_feasible"):
   package["recommended_strategy"]=strategy;package["recommended_plan"]={"items":candidate.get("items",[])};package["business_metrics"]=candidate.get("business_metrics",{});package["critic"]=candidate.get("critic",{});package["stress_tests"]=candidate.get("stress_tests") or [];package["status"]="completed"
  else:
   package["recommended_strategy"]=None;package["recommended_plan"]={"items":[]};package["business_metrics"]={};package["critic"]=candidate.get("critic",{});package["status"]="completed_with_no_feasible_recommendation"

 @staticmethod
 def _what_if_comparison(baseline,hypothetical):
  def delta(a,b):return None if a is None or b is None else b-a
  left={row.ingredient_id:row for row in baseline.procurement_rows};right={row.ingredient_id:row for row in hypothetical.procurement_rows};changes=[]
  for ingredient_id in sorted(set(left)|set(right)):
   a,b=left.get(ingredient_id),right.get(ingredient_id);changes.append({"ingredient_id":ingredient_id,"baseline_quantity":a.quantity if a else None,"hypothetical_quantity":b.quantity if b else None,"quantity_delta":delta(a.quantity if a else None,b.quantity if b else None),"baseline_supplier_id":a.supplier_id if a else None,"hypothetical_supplier_id":b.supplier_id if b else None,"baseline_arrival_date":a.arrival_date if a else None,"hypothetical_arrival_date":b.arrival_date if b else None})
  return {"recommendation_changed":baseline.recommendation.available!=hypothetical.recommendation.available or baseline.recommendation.strategy!=hypothetical.recommendation.strategy,"baseline_strategy":baseline.recommendation.strategy,"hypothetical_strategy":hypothetical.recommendation.strategy,"purchase_cost_delta":delta(baseline.recommendation.total_purchase_cost,hypothetical.recommendation.total_purchase_cost),"expected_fill_rate_delta":delta(baseline.risk.expected_fill_rate,hypothetical.risk.expected_fill_rate),"stockout_probability_delta":delta(baseline.risk.stockout_probability,hypothetical.risk.stockout_probability),"shortage_quantity_delta":delta(baseline.risk.shortage_quantity,hypothetical.risk.shortage_quantity),"waste_quantity_delta":delta(baseline.risk.waste_quantity,hypothetical.risk.waste_quantity),"order_changes":changes,"warnings_added":sorted(set(hypothetical.critic.warnings)-set(baseline.critic.warnings)),"warnings_removed":sorted(set(baseline.critic.warnings)-set(hypothetical.critic.warnings)),"hard_violations_added":sorted(set(hypothetical.critic.hard_violations)-set(baseline.critic.hard_violations)),"hard_violations_removed":sorted(set(baseline.critic.hard_violations)-set(hypothetical.critic.hard_violations))}

 @staticmethod
 def _decision_package(rid,store,forecast,mode,result,request,baseline,demands,scenario_metadata=None,forecast_trace=None,shortage_economics=None):
  from app.services.business_metrics_service import build_business_metrics
  def metric(sim):
   return build_business_metrics(purchase_cost=None, simulation=sim, recommended=True)
  scenario_metadata=scenario_metadata or {};strategies={};warnings=set(scenario_metadata.get("warnings",[]));recommended=result.recommended_strategy.lower() if result.recommended_strategy else None
  for name,evaluation in result.evaluations.items():
   sim=evaluation.simulation;critic=evaluation.critic;warnings.update(evaluation.plan.warnings);warnings.update(critic.warnings)
   strategies[name.lower()]={"strategy":name.lower(),"is_feasible":critic.passed,"purchase_cost":evaluation.plan.purchase_cost,"expected_recourse_cost":evaluation.plan.expected_recourse_cost,"business_metrics":build_business_metrics(purchase_cost=evaluation.plan.purchase_cost,simulation=sim,recommended=True,risk_simulation=evaluation.risk_simulation,risk_metadata=request.risk_evaluation_metadata) if sim else {},"items":[x.model_dump(mode="json") for x in evaluation.plan.orders],"critic":{"status":"pass" if critic.passed else "fail","findings":[{"code":x,"severity":"error","evidence":critic.details.get("finding_evidence",{}).get(x,{})} for x in critic.hard_violations],"warnings":critic.warnings,"checks":critic.checks,"details":critic.details},"stress_tests":evaluation.stress_simulation.model_dump(mode="json") if evaluation.stress_simulation else None,"technical_metrics":evaluation.plan.provenance}
  selected=strategies.get(recommended or "",{});status="completed" if recommended else "completed_with_no_feasible_recommendation"
  reasons=[]
  for item in selected.get("items",[]):
   if item.get("pack_count",0) and item.get("order_quantity",0) > 0: reasons.append({"code":"PACK_SIZE_ROUNDING","entity_id":item.get("ingredient_id"),"evidence":{"pack_size":item.get("pack_size"),"final_order_quantity":item.get("order_quantity")}})
  top_metrics=selected.get("business_metrics") if selected else build_business_metrics(purchase_cost=None,simulation=None,recommended=False)
  return {"decision_run_id":rid,"store_id":store,"as_of_date":forecast.cutoff_date.isoformat(),"horizon_days":forecast.horizon_days,"status":status,"engine_mode":mode,"recommended_strategy":recommended,"business_metrics":top_metrics,"recommended_plan":{"items":selected.get("items",[])},"ingredient_demand":[{"ingredient_id":x.ingredient_id,"target_date":x.target_date.isoformat(),"unit":x.unit,"p25":float(x.p25),"p50":float(x.p50),"p75":float(x.p75),"contributions":json.loads(x.contributions_json)} for x in demands],"inventory_risk":baseline.model_dump(mode="json"),"strategies":strategies,"stress_tests":selected.get("stress_tests") or [],"critic":selected.get("critic",{}),"reason_codes":reasons,"warnings":sorted(warnings),"technical_metrics":{"scenario_count":len(request.demand_scenarios),"scenario_method":scenario_metadata.get("method","quantile_design_fallback"),"random_seed":request.seed,"optimizer_type":result.provenance["candidate_engine"],"cvar_alpha":None,"core_version":"local","stochastic_saa_enabled":request.stochastic,"risk_evaluation_status":request.risk_evaluation_metadata.get("status"),"risk_evaluation_sample_count":(len(request.demand_scenarios) if request.stochastic else len(request.risk_demand_scenarios)),"baseline_engine":"lot_level_fefo_v1","scenario_diagnostics":scenario_metadata.get("diagnostics",{}),"forecast_trace":forecast_trace or {},"shortage_economics":shortage_economics or {}}}
