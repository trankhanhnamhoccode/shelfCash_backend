from datetime import date, datetime
from decimal import Decimal
from typing import Any,Literal
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from app.dependencies import get_completion_service, get_decision_planning_service, get_forecast_service, require_api_key
from app.schemas.forecast import LegacyForecastMetadataResponse, LegacyForecastResultResponse
from app.schemas.planning import LegacyPlanMetadataResponse, LegacyPlanResultResponse

class Strict(BaseModel): model_config=ConfigDict(extra="forbid")
class CountLine(Strict): lot_id:str;counted_quantity:Decimal=Field(ge=0,allow_inf_nan=False);unit:str;note:str|None=None
class CountIn(Strict): counted_at:datetime;lines:list[CountLine]=Field(min_length=1)
class AdjustmentLine(Strict): lot_id:str;expected_version:int=Field(ge=1);quantity_delta:Decimal=Field(allow_inf_nan=False);unit:str;reason:str;note:str|None=None
class AdjustmentIn(Strict): occurred_at:datetime;reference:str|None=None;lines:list[AdjustmentLine]=Field(min_length=1)
class ForecastIn(Strict): cutoff_date:date;horizon_days:int=Field(ge=1,le=90);quantiles:list[float];scope:dict[str,list[str]];use_latest_calendar:bool=True
class PlanIn(Strict): forecast_run_id:str;strategy:str;budget_limit:int=Field(ge=0);as_of_date:date;include_open_purchase_orders:bool=True
class SettingsIn(Strict): monthly_budget:int=Field(ge=0);forecast_horizon:int=Field(ge=1,le=90);default_strategy:str;version:int=Field(ge=1)
class CalendarItemIn(Strict): date:date;holiday:bool;promotion:bool;promotion_note:str|None=None
class CalendarIn(Strict): items:list[CalendarItemIn]=Field(min_length=1)
class SalesRecordIn(Strict):
    external_record_id:str=Field(min_length=1);date:date;product_id:str;quantity:Decimal=Field(ge=0,allow_inf_nan=False);unit_price:int=Field(ge=0);promotion:bool=False
class SalesBatchIn(Strict): source:str;records:list[SalesRecordIn]=Field(min_length=1)
class PurchaseRecordIn(Strict):
    external_record_id:str|None=None;date:date;ingredient_id:str;supplier_id:str;quantity:Decimal=Field(gt=0,allow_inf_nan=False);unit:str;unit_cost:int=Field(ge=0);expiry_date:date|None=None;supplier_lot_code:str|None=None
class PurchaseBatchIn(Strict): source:str;inventory_effect:str;records:list[PurchaseRecordIn]=Field(min_length=1)
class SupplierTermIn(Strict):
    ingredient_id:str;supplier_id:str;unit_cost:int=Field(ge=0);moq:Decimal=Field(ge=0);pack_size:Decimal=Field(gt=0);lead_time_days:int=Field(ge=0);shelf_life_days:int|None=Field(default=None,ge=0);unit:str;version:int|None=Field(default=None,ge=1)
class SupplierTermOut(Strict):
    constraint_id:str;ingredient_id:str;supplier_id:str;supplier:str;unit_cost:int;moq:Decimal;pack_size:Decimal;order_unit:str|None;available_delivery_days:list[int]|None;lead_time_days:int;shelf_life_days:int|None;unit:str;version:int;active:bool
class SupplierTermList(Strict): items:list[SupplierTermOut];page:int;page_size:int;total:int
class InventoryConstraintOut(Strict):
    constraint_id:str;ingredient_id:str|None;ingredient_name:str|None
    constraint_type:str=Field(description="Registered business constraint type; determines scope, dimension, units, and value rules.")
    value:Decimal=Field(description="Canonical value for the constraint type (for example days or a 0..1 ratio).")
    unit:str|None=Field(description="Type-dependent canonical unit: physical quantity unit, day, ratio, or VND.")
    currency:str|None=None
    effective_date:date;end_date:date|None;version:int;active:bool
class InventoryConstraintList(Strict): store_id:str;as_of_date:date|None;items:list[InventoryConstraintOut]
class InventoryConstraintCreateIn(Strict):
    ingredient_id:str|None=None;constraint_type:str;value:Decimal=Field(allow_inf_nan=False);unit:str|None=None
    currency:str|None=None;effective_date:date;note:str|None=Field(default=None,max_length=500)
class InventoryConstraintPatchIn(Strict):
    expected_version:int=Field(ge=1);value:Decimal=Field(allow_inf_nan=False);unit:str|None=None;currency:str|None=None
    effective_date:date|None=None;note:str|None=Field(default=None,max_length=500)
    correction_mode:Literal["replace_same_effective_date"]|None=None
class InventoryConstraintDeactivateIn(Strict):expected_version:int=Field(ge=1);end_date:date;note:str|None=Field(default=None,max_length=500)
class InventoryConstraintWriteItem(Strict):
    constraint_id:str;store_id:str;ingredient_id:str|None;constraint_type:str;value:Decimal;unit:str|None;currency:str|None
    effective_date:date;end_date:date|None;version:int;active:bool;note:str|None;superseded_by_constraint_id:str|None
class InventoryConstraintHistoryItem(Strict):
    constraint_id:str;version:int;effective_date:date;end_date:date|None;active:bool;superseded_by_constraint_id:str|None
class InventoryConstraintWriteOut(Strict):constraint:InventoryConstraintWriteItem;history:list[InventoryConstraintHistoryItem]
class POCreateLine(Strict): recommendation_id:str;order_quantity_override:Decimal|None=Field(default=None,ge=0,allow_inf_nan=False)
class POCreateIn(Strict): plan_run_id:str;lines:list[POCreateLine]=Field(min_length=1)
class POLineUpdate(Strict): po_line_id:str;order_quantity:Decimal=Field(gt=0,allow_inf_nan=False)
class POPatchIn(Strict): version:int=Field(ge=1);line_updates:list[POLineUpdate]=Field(min_length=1)
class POConfirmIn(Strict): version:int=Field(ge=1);confirmed_at:datetime
class ReceiveLotIn(Strict): quantity:Decimal=Field(gt=0,allow_inf_nan=False);expiry_date:date|None=None;supplier_lot_code:str|None=None
class ReceiveLineIn(Strict): po_line_id:str;lots:list[ReceiveLotIn]=Field(min_length=1)
class POReceiveIn(Strict): version:int=Field(ge=1);received_at:datetime;delivery_reference:str;lines:list[ReceiveLineIn]=Field(min_length=1)
router=APIRouter(tags=["contract"],dependencies=[Depends(require_api_key)])
def idem(v:str|None=Header(None,alias="Idempotency-Key")):return v

@router.get("/stores/{store_id}/bootstrap")
def bootstrap(store_id:str,s=Depends(get_completion_service)):return s.bootstrap(store_id)
@router.get("/stores/{store_id}/dashboard")
def dashboard(store_id:str,s=Depends(get_completion_service)):return s.dashboard(store_id)
@router.post("/stores/{store_id}/inventory-counts",status_code=201)
def counts(store_id:str,b:CountIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("inventory_count",store_id,b,k)
@router.post("/stores/{store_id}/inventory-adjustments",status_code=201)
def adjustments(store_id:str,b:AdjustmentIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("inventory_adjustment",store_id,b,k)
@router.post("/stores/{store_id}/sales-history/batch",status_code=201)
def sales_batch(store_id:str,b:SalesBatchIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("sales_batch",store_id,b,k)
@router.post("/stores/{store_id}/purchase-history/batch",status_code=201)
def purchase_batch(store_id:str,b:PurchaseBatchIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("purchase_batch",store_id,b,k)
@router.get("/stores/{store_id}/supplier-constraints",response_model=SupplierTermList)
def supplier_get(store_id:str,s=Depends(get_completion_service)):return s.supplier_list(store_id)
@router.get("/stores/{store_id}/inventory-constraints",response_model=InventoryConstraintList)
def inventory_constraints_get(store_id:str,ingredient_id:str|None=None,constraint_type:str|None=None,as_of_date:date|None=None,s=Depends(get_completion_service)):
 return s.inventory_constraints(store_id,ingredient_id,constraint_type,as_of_date)
@router.post("/stores/{store_id}/inventory-constraints",status_code=201,response_model=InventoryConstraintWriteOut)
def inventory_constraints_post(store_id:str,b:InventoryConstraintCreateIn,k=Depends(idem),s=Depends(get_completion_service)):
 return s.inventory_constraint_write("create",store_id,b,k)
@router.patch("/stores/{store_id}/inventory-constraints/{constraint_id}",response_model=InventoryConstraintWriteOut)
def inventory_constraints_patch(store_id:str,constraint_id:str,b:InventoryConstraintPatchIn,k=Depends(idem),s=Depends(get_completion_service)):
 return s.inventory_constraint_write("update",store_id,b,k,constraint_id)
@router.post("/stores/{store_id}/inventory-constraints/{constraint_id}/deactivate",response_model=InventoryConstraintWriteOut)
def inventory_constraints_deactivate(store_id:str,constraint_id:str,b:InventoryConstraintDeactivateIn,k=Depends(idem),s=Depends(get_completion_service)):
 return s.inventory_constraint_write("deactivate",store_id,b,k,constraint_id)
@router.post("/stores/{store_id}/supplier-constraints",status_code=201)
def supplier_post(store_id:str,b:SupplierTermIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("supplier_create",store_id,b,k)
@router.put("/stores/{store_id}/supplier-constraints/{constraint_id}")
def supplier_put(store_id:str,constraint_id:str,b:SupplierTermIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("supplier_update",store_id,b,k,constraint_id)
@router.put("/stores/{store_id}/settings")
def settings(store_id:str,b:SettingsIn,s=Depends(get_completion_service)):return s.write("settings",store_id,b,None)
@router.put("/stores/{store_id}/calendar-features")
def calendar(store_id:str,b:CalendarIn,s=Depends(get_completion_service)):return s.write("calendar",store_id,b,None)
@router.post("/stores/{store_id}/forecast-runs", response_model=LegacyForecastMetadataResponse)
def forecast_post(store_id:str,b:ForecastIn,request:Request,k=Depends(idem),s=Depends(get_forecast_service)):
 return s.create_legacy_run(store_id,b,k,getattr(request.state,"request_id",None))
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}", response_model=LegacyForecastMetadataResponse)
def forecast_get(store_id:str,forecast_run_id:str,s=Depends(get_forecast_service)):return s.get_metadata(forecast_run_id,store_id)
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}/result", response_model=LegacyForecastResultResponse)
def forecast_result(store_id:str,forecast_run_id:str,s=Depends(get_forecast_service)):return s.get_legacy_result(forecast_run_id,store_id)
@router.post("/stores/{store_id}/plan-runs",response_model=LegacyPlanMetadataResponse)
def plan_post(store_id:str,b:PlanIn,request:Request,k=Depends(idem),s=Depends(get_decision_planning_service)):
 return s.create_legacy_plan(store_id,b,k,getattr(request.state,"request_id",None))
@router.get("/stores/{store_id}/plan-runs/{plan_run_id}",response_model=LegacyPlanMetadataResponse)
def plan_get(store_id:str,plan_run_id:str,s=Depends(get_decision_planning_service)):return s.get_legacy_plan_metadata(store_id,plan_run_id)
@router.get("/stores/{store_id}/plan-runs/{plan_run_id}/result",response_model=LegacyPlanResultResponse)
def plan_result(store_id:str,plan_run_id:str,s=Depends(get_decision_planning_service)):return s.get_legacy_plan_result(store_id,plan_run_id)
@router.post("/stores/{store_id}/purchase-orders",status_code=201)
def po_post(store_id:str,b:POCreateIn,k=Depends(idem),s=Depends(get_completion_service)):return s.po("create",store_id,None,b,k)
@router.get("/stores/{store_id}/purchase-orders")
def po_list(store_id:str,s=Depends(get_completion_service)):return s.po("list",store_id)
@router.get("/stores/{store_id}/purchase-orders/{po_id}")
def po_get(store_id:str,po_id:str,s=Depends(get_completion_service)):return s.po("get",store_id,po_id)
@router.patch("/stores/{store_id}/purchase-orders/{po_id}")
def po_patch(store_id:str,po_id:str,b:POPatchIn,s=Depends(get_completion_service)):return s.po("patch",store_id,po_id,b)
@router.post("/stores/{store_id}/purchase-orders/{po_id}/confirm")
def po_confirm(store_id:str,po_id:str,b:POConfirmIn,s=Depends(get_completion_service)):return s.po("confirm",store_id,po_id,b)
@router.post("/stores/{store_id}/purchase-orders/{po_id}/receive",status_code=201)
def po_receive(store_id:str,po_id:str,b:POReceiveIn,k=Depends(idem),s=Depends(get_completion_service)):return s.po("receive",store_id,po_id,b,k)
