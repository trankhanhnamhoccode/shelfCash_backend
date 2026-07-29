from datetime import date, datetime
from decimal import Decimal
from typing import Any
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from app.dependencies import get_completion_service, require_api_key

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
    external_record_id:str=Field(min_length=1);date:date;ingredient_id:str;supplier_id:str;quantity:Decimal=Field(gt=0,allow_inf_nan=False);unit:str;unit_cost:int=Field(ge=0);expiry_date:date|None=None;supplier_lot_code:str|None=None
class PurchaseBatchIn(Strict): source:str;inventory_effect:str;records:list[PurchaseRecordIn]=Field(min_length=1)
class SupplierTermIn(Strict):
    ingredient_id:str;supplier_id:str;unit_cost:int=Field(ge=0);moq:Decimal=Field(ge=0);pack_size:Decimal=Field(gt=0);lead_time_days:int=Field(ge=0);safety_stock:Decimal=Field(ge=0);capacity:Decimal|None=Field(default=None,ge=0);unit:str;version:int|None=Field(default=None,ge=1)
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
@router.get("/stores/{store_id}/supplier-constraints")
def supplier_get(store_id:str,s=Depends(get_completion_service)):return s.supplier_list(store_id)
@router.post("/stores/{store_id}/supplier-constraints",status_code=201)
def supplier_post(store_id:str,b:SupplierTermIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("supplier_create",store_id,b,k)
@router.put("/stores/{store_id}/supplier-constraints/{constraint_id}")
def supplier_put(store_id:str,constraint_id:str,b:SupplierTermIn,k=Depends(idem),s=Depends(get_completion_service)):return s.write("supplier_update",store_id,b,k,constraint_id)
@router.put("/stores/{store_id}/settings")
def settings(store_id:str,b:SettingsIn,s=Depends(get_completion_service)):return s.write("settings",store_id,b,None)
@router.put("/stores/{store_id}/calendar-features")
def calendar(store_id:str,b:CalendarIn,s=Depends(get_completion_service)):return s.write("calendar",store_id,b,None)
@router.post("/stores/{store_id}/forecast-runs",status_code=202)
def forecast_post(store_id:str,b:ForecastIn,k=Depends(idem),s=Depends(get_completion_service)):return s.forecast_create(store_id,b,k)
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}")
def forecast_get(store_id:str,forecast_run_id:str,s=Depends(get_completion_service)):return s.forecast_get(store_id,forecast_run_id,False)
@router.get("/stores/{store_id}/forecast-runs/{forecast_run_id}/result")
def forecast_result(store_id:str,forecast_run_id:str,s=Depends(get_completion_service)):return s.forecast_get(store_id,forecast_run_id,True)
@router.post("/stores/{store_id}/plan-runs",status_code=202)
def plan_post(store_id:str,b:PlanIn,k=Depends(idem),s=Depends(get_completion_service)):return s.plan_create(store_id,b,k)
@router.get("/stores/{store_id}/plan-runs/{plan_run_id}")
def plan_get(store_id:str,plan_run_id:str,s=Depends(get_completion_service)):return s.plan_get(store_id,plan_run_id,False)
@router.get("/stores/{store_id}/plan-runs/{plan_run_id}/result")
def plan_result(store_id:str,plan_run_id:str,s=Depends(get_completion_service)):return s.plan_get(store_id,plan_run_id,True)
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
