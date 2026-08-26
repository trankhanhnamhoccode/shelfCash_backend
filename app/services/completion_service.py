import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.core.exceptions import (
 BudgetExceededError, BusinessIdentityConflictError, DuplicateRequestError, InvalidStateTransitionError,
 ModelNotReadyError, ResourceNotFoundError, ValidationError, VersionConflictError,
)
from app.core.provenance import canonical_hash, normalized_optional_identifier, purchase_receipt_identity
from app.services.sales_usage_reconciliation import reconcile_usage_from_sales
from app.core.business_constraints import constraint_definition
from app.core.units import convert_quantity
from shelfcash_core.optimization.expiry import resolve_inbound_expiry
from app.models.business import (
 CalendarFeatureModel, IngredientAliasModel, IngredientModel, InventoryLotModel, InventoryMovementModel,
 ProductBundleLineModel, ProductModel, PurchaseReceiptModel, SalesDailyModel, StoreSettingsModel, SupplierModel,
 SupplierIngredientTermModel, RecipeVersionModel, RecipeLineModel, UsageDailyModel,
)
from app.repositories.inventory_constraints import InventoryConstraintRepository
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService
from app.models.operations import (
 BudgetPeriodModel, ForecastRunModel, PlanRunModel, RecommendationModel,
 PurchaseOrderModel, PurchaseOrderLineModel,
)
from app.repositories.stores import StoreRepository

class CompletionService:
 def __init__(self,factory,operational,forecast_service,decision_planning_service=None):self.factory=factory;self.operational=operational;self.forecast_service=forecast_service;self.decision_planning_service=decision_planning_service
 def bootstrap(self,store):
  with self.factory() as s:
   x=StoreRepository(s).get_required(store)
   products=[{"product_id":p.product_id,"product":p.product,"sku":p.sku,"price":p.price,"active":p.active,"version":p.version} for p in s.scalars(select(ProductModel).where(ProductModel.store_id==store).order_by(ProductModel.normalized_name))]
   menu_products=list(s.scalars(select(ProductModel).where(ProductModel.store_id==store).order_by(ProductModel.normalized_name)))
   from app.services.menu_service import MenuService
   menu_serializer=MenuService(None);menu_graph=menu_serializer._load_graph(s,store,menu_products)
   menu=[menu_serializer.serialize(p,menu_graph) for p in menu_products]
   menu_updated_at=max((p.updated_at for p in menu_products),default=None)
   ingredients=[{"ingredient_id":i.ingredient_id,"ingredient":i.ingredient,"sku":i.sku,"base_unit":i.base_unit,"active":i.active,"version":i.version} for i in s.scalars(select(IngredientModel).where(IngredientModel.store_id==store).order_by(IngredientModel.normalized_name))]
   aliases=[{"alias_id":a.alias_id,"ingredient_id":a.ingredient_id,"alias":a.alias} for a in s.scalars(select(IngredientAliasModel).where(IngredientAliasModel.store_id==store).order_by(IngredientAliasModel.normalized_alias))]
   active_versions=list(s.scalars(select(RecipeVersionModel).where(
    RecipeVersionModel.store_id==store,RecipeVersionModel.effective_from<=date.today(),
    (RecipeVersionModel.effective_to.is_(None))|(RecipeVersionModel.effective_to>=date.today()),
   ).order_by(RecipeVersionModel.product_id,RecipeVersionModel.version.desc())))
   active_by_product={}
   for rv in active_versions:active_by_product.setdefault(rv.product_id,rv)
   version_ids=[rv.recipe_version_id for rv in active_by_product.values()]
   component_rows=[] if not version_ids else list(s.execute(select(RecipeLineModel,IngredientModel).join(
    IngredientModel,IngredientModel.ingredient_id==RecipeLineModel.ingredient_id
   ).where(RecipeLineModel.recipe_version_id.in_(version_ids)).order_by(RecipeLineModel.recipe_version_id,IngredientModel.normalized_name)))
   components_by_version={version_id:[] for version_id in version_ids}
   for line,ingredient in component_rows:
    components_by_version[line.recipe_version_id].append({
     "ingredient_id":ingredient.ingredient_id,"ingredient":ingredient.ingredient,
     "quantity":format(line.quantity.normalize(),"f"),"unit":line.unit,
    })
   product_by_id={p["product_id"]:p for p in products}
   recipes=[{
    "product_id":rv.product_id,"sku":product_by_id[rv.product_id]["sku"],
    "recipe_version_id":rv.recipe_version_id,"version":rv.version,
    "effective_from":rv.effective_from,"effective_to":rv.effective_to,
    "components":components_by_version[rv.recipe_version_id],
   } for rv in active_by_product.values() if rv.product_id in product_by_id]
   fr=s.scalar(select(ForecastRunModel).where(ForecastRunModel.store_id==store).order_by(ForecastRunModel.created_at.desc()))
   pr=s.scalar(select(PlanRunModel).where(PlanRunModel.store_id==store).order_by(PlanRunModel.created_at.desc()))
   pos=[self._po_public(s,p) for p in s.scalars(select(PurchaseOrderModel).where(PurchaseOrderModel.store_id==store,PurchaseOrderModel.status.in_(["draft","ordered","partially_received"])))]
   settings=self.operational.settings(store)
   calendar=self.operational.calendar(store,1,200,date.today(),date.today()+timedelta(days=settings["forecast_horizon"]-1)).get("items")
   calendar_by_date={item["date"]:item for item in calendar}
   future_calendar=[]
   for offset in range(settings["forecast_horizon"]):
    day=date.today()+timedelta(days=offset)
    future_calendar.append(calendar_by_date.get(day,{"date":day,"weekday":day.strftime("%A"),"weekend":day.weekday()>=5,"holiday":False,"promotion":False,"promotion_note":None}))
  return {"today":date.today(),"store":{"store_id":x.store_id,"store_name":x.store_name,"timezone":x.timezone,"currency":x.currency},
   "ingredients":ingredients,"inventory":self.operational.inventory(store,1,200).get("items"),"products":products,"menu":menu,"recipes":recipes,
   "supplier_constraints":self.supplier_list(store)["items"],"aliases":aliases,
   "future_calendar":future_calendar,"settings":settings,
   "latest_runs":{"forecast_run_id":fr.forecast_run_id if fr else None,"plan_run_id":pr.plan_run_id if pr else None},
   "open_purchase_orders":pos,"data_freshness":{"menu_updated_at":menu_updated_at}}
 def dashboard(self,store):
  inv=self.operational.inventory(store,1,200)["items"];settings=self.operational.settings(store)
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   ingredient_count=s.scalar(select(func.count()).select_from(IngredientModel).where(IngredientModel.store_id==store))
   product_count=s.scalar(select(func.count()).select_from(ProductModel).where(ProductModel.store_id==store,ProductModel.active.is_(True)))
   open_po=s.scalar(select(func.count()).select_from(PurchaseOrderModel).where(PurchaseOrderModel.store_id==store,PurchaseOrderModel.status.in_(["draft","ordered","partially_received"])))
   latest_sales=s.scalar(select(func.max(SalesDailyModel.date)).where(SalesDailyModel.store_id==store))
   fr=s.scalar(select(ForecastRunModel).where(ForecastRunModel.store_id==store).order_by(ForecastRunModel.created_at.desc()))
   pr=s.scalar(select(PlanRunModel).where(PlanRunModel.store_id==store).order_by(PlanRunModel.created_at.desc()))
  return {"ingredient_count":ingredient_count,"active_product_count":product_count,"inventory_lot_count":len(inv),
   "stockout_count":sum(x["status"]=="stockout" for x in inv),"low_stock_count":sum(x["status"]=="low" for x in inv),
   "expiring_lot_count":sum(x["status"]=="expiring" for x in inv),"open_po_count":open_po,
   **{k:settings[k] for k in ("monthly_budget","reserved_budget","spent_budget","remaining_budget")},
   "latest_sales_date":latest_sales,"latest_forecast_status":fr.status if fr else None,"latest_plan_status":pr.status if pr else None,"data_notes":[]}
 def supplier_list(self,store):
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   rows=s.execute(select(SupplierIngredientTermModel,SupplierModel).join(SupplierModel,SupplierModel.supplier_id==SupplierIngredientTermModel.supplier_id).where(SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.active.is_(True)).order_by(SupplierModel.normalized_name,SupplierIngredientTermModel.ingredient_id))
   items=[{"constraint_id":x.constraint_id,"ingredient_id":x.ingredient_id,"supplier_id":x.supplier_id,"supplier":sup.supplier,"unit_cost":x.unit_cost,"moq":str(x.moq),"pack_size":str(x.pack_size),"order_unit":x.order_unit,"available_delivery_days":None if x.available_delivery_days is None else json.loads(x.available_delivery_days),"lead_time_days":x.lead_time_days,"shelf_life_days":x.shelf_life_days,"unit":x.unit,"version":x.version,"active":x.active} for x,sup in rows]
   return {"items":items,"page":1,"page_size":50,"total":len(items)}
 def inventory_constraints(self,store,ingredient_id=None,constraint_type=None,as_of_date=None):
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   canonical_type=constraint_definition(constraint_type)[0] if constraint_type is not None else None
   rows=InventoryConstraintRepository(s).list(store,ingredient_id,canonical_type,as_of_date)
   names={x.ingredient_id:x.ingredient for x in s.scalars(select(IngredientModel).where(IngredientModel.store_id==store))}
   return {"store_id":store,"as_of_date":as_of_date,"items":[{"constraint_id":x.constraint_id,"ingredient_id":x.ingredient_id,
    "ingredient_name":names.get(x.ingredient_id),"constraint_type":x.constraint_type,"value":str(x.value),"unit":x.unit,"currency":x.currency,
   "effective_date":x.effective_date,"end_date":x.end_date,"version":x.version,"active":x.active} for x in rows]}
 def inventory_constraint_write(self,operation,store,body,key,constraint_id=None):
  from app.services.inventory_constraint_write_service import InventoryConstraintWriteService
  service=InventoryConstraintWriteService(self.factory)
  if operation=="create":return service.create(store,body,key)
  if operation=="update":return service.update(store,constraint_id,body,key)
  if operation=="deactivate":return service.deactivate(store,constraint_id,body,key)
  raise ValidationError("Unsupported inventory constraint operation.")
 def write(self,kind,store,body,key,target=None):
  if kind in {"inventory_count","inventory_adjustment"}:
   return self._inventory_write(kind,store,body,key)
  if kind in {"sales_batch","purchase_batch"}:
   return self._history_batch(kind,store,body,key)
  if kind in {"supplier_create","supplier_update"}:
   return self._supplier_write(kind,store,body,key,target)
  if kind=="settings":
   with self.factory() as s:
    StoreRepository(s).get_required(store);x=s.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id==store))
    if x and x.version!=body.version:raise VersionConflictError(details={"expected_version":body.version,"current_version":x.version})
    if not x:x=StoreSettingsModel(setting_id=str(uuid4()),store_id=store);s.add(x)
    changed=(x.monthly_budget,x.forecast_horizon,x.default_strategy)!=(body.monthly_budget,body.forecast_horizon,body.default_strategy)
    x.monthly_budget=body.monthly_budget;x.forecast_horizon=body.forecast_horizon;x.default_strategy=body.default_strategy
    if changed:x.version=(x.version+1 if x.version else 1)
    period=date.today().strftime("%Y-%m");bp=s.scalar(select(BudgetPeriodModel).where(BudgetPeriodModel.store_id==store,BudgetPeriodModel.period==period))
    if not bp:s.add(BudgetPeriodModel(budget_period_id=str(uuid4()),store_id=store,period=period,monthly_budget=body.monthly_budget,reserved_budget=0,spent_budget=0))
    else:bp.monthly_budget=body.monthly_budget
    AuditService(AuditLogRepository(s)).record(store_id=store,action="settings_updated",resource_type="store_settings",resource_id=x.setting_id,after={"version":x.version},source="api")
    s.commit();return self.operational.settings(store)
  if kind=="calendar":return self._calendar_write(store,body)
  raise ValidationError("Operation requires the completed transactional adapter.",{"operation":kind})
 def _inventory_write(self,kind,store,body,key):
  path=f"/api/v1/stores/{store}/"+("inventory-counts" if kind=="inventory_count" else "inventory-adjustments")
  payload=body.model_dump(mode="json");request_hash=canonical_hash(payload)
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   if len({x.lot_id for x in body.lines})!=len(body.lines):raise ValidationError("lot_id bị lặp.",{"reason":"duplicate_lot"})
   idem=IdempotencyService(IdempotencyRepository(s))
   if key:
    replay=idem.register(store_id=store,endpoint=path,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:s.rollback();return json.loads(replay.record.response_body_json)
   output=[]
   allowed={"waste","expired","damaged","correction_increase","correction_decrease","other"}
   for line in body.lines:
    lot=s.scalar(select(InventoryLotModel).where(InventoryLotModel.store_id==store,InventoryLotModel.lot_id==line.lot_id))
    if not lot:raise ResourceNotFoundError(details={"resource":"inventory_lot","lot_id":line.lot_id})
    before=Decimal(s.scalar(select(func.coalesce(func.sum(InventoryMovementModel.quantity_delta),0)).where(InventoryMovementModel.store_id==store,InventoryMovementModel.lot_id==lot.lot_id)))
    if kind=="inventory_count":
     target=convert_quantity(line.counted_quantity,line.unit,lot.unit);delta=target-before;occurred=body.counted_at;mtype="physical_count_adjustment"
     lot.snapshot_date=body.counted_at.date()
    else:
     if line.reason not in allowed:raise ValidationError("reason không hợp lệ.")
     delta=convert_quantity(line.quantity_delta,line.unit,lot.unit)
     if delta==0 or (line.reason in {"waste","expired","damaged","correction_decrease"} and delta>=0) or (line.reason=="correction_increase" and delta<=0) or (line.reason=="other" and not line.note):raise ValidationError("Dấu quantity_delta không phù hợp reason.")
     if lot.version!=line.expected_version:raise VersionConflictError(details={"expected_version":line.expected_version,"current_version":lot.version})
     target=before+delta;occurred=body.occurred_at;mtype={"expired":"expiry","waste":"waste"}.get(line.reason,"manual_adjustment")
    if target<0:raise ValidationError("Tồn kho sau điều chỉnh không được âm.",{"lot_id":lot.lot_id})
    movement_id=None
    if delta:
     movement_id=str(uuid4());s.add(InventoryMovementModel(movement_id=movement_id,store_id=store,lot_id=lot.lot_id,movement_type=mtype,quantity_delta=delta,unit=lot.unit,occurred_at=occurred,source="api",source_id=getattr(body,"reference",None),note=line.note))
    lot.version+=1
    output.append({"movement_id":movement_id,"lot_id":lot.lot_id,"before_quantity":str(before),"quantity_delta":str(delta),"after_quantity":str(target),"version":lot.version})
   result=({"inventory_count_id":str(uuid4()),"adjustments":output,"inventory":[]} if kind=="inventory_count" else {"inventory_adjustment_id":str(uuid4()),"occurred_at":body.occurred_at,"movements":output})
   AuditService(AuditLogRepository(s)).record(store_id=store,action=kind,resource_type="inventory",resource_id=result.get("inventory_count_id") or result["inventory_adjustment_id"],after={"line_count":len(output)},source="api")
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=path,http_method="POST",idempotency_key=key);rec.response_status=201;rec.response_body_json=json.dumps(result,default=str,ensure_ascii=False)
   s.commit();return result
 def _calendar_write(self,store,body):
  if len({x["date"] if isinstance(x,dict) else x.date for x in body.items})!=len(body.items):raise ValidationError("date bị lặp.")
  with self.factory() as s:
   StoreRepository(s).get_required(store);created=updated=unchanged=0;out=[]
   for raw in body.items:
    item=raw if isinstance(raw,dict) else raw.model_dump();day=item["date"];holiday=item["holiday"];promotion=item["promotion"];note=item.get("promotion_note") if promotion else None
    x=s.scalar(select(CalendarFeatureModel).where(CalendarFeatureModel.store_id==store,CalendarFeatureModel.date==day))
    same=x and (x.is_holiday,x.is_promotion,x.promotion_name)==(holiday,promotion,note)
    if same:unchanged+=1
    elif x:x.is_holiday=holiday;x.is_promotion=promotion;x.promotion_name=note;x.is_weekend=day.weekday()>=5;updated+=1
    else:x=CalendarFeatureModel(calendar_feature_id=str(uuid4()),store_id=store,date=day,is_weekend=day.weekday()>=5,is_holiday=holiday,is_store_closed=False,is_promotion=promotion,promotion_name=note,source="api");s.add(x);created+=1
    out.append({"date":day,"weekday":day.strftime("%A"),"weekend":day.weekday()>=5,"holiday":holiday,"promotion":promotion,"promotion_note":note})
   AuditService(AuditLogRepository(s)).record(store_id=store,action="calendar_upserted",resource_type="calendar",resource_id=store,after={"created_count":created,"updated_count":updated},source="api");s.commit()
   return {"created_count":created,"updated_count":updated,"unchanged_count":unchanged,"items":out}
 def _supplier_write(self,kind,store,b,key,target):
  path=f"/api/v1/stores/{store}/supplier-constraints"+(f"/{target}" if target else "")
  request_hash=canonical_hash(b.model_dump(mode="json"))
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   if key:
    idem=IdempotencyService(IdempotencyRepository(s));replay=idem.register(store_id=store,endpoint=path,http_method="POST" if kind=="supplier_create" else "PUT",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:s.rollback();return json.loads(replay.record.response_body_json)
   ingredient=s.scalar(select(IngredientModel).where(IngredientModel.store_id==store,IngredientModel.ingredient_id==b.ingredient_id));supplier=s.scalar(select(SupplierModel).where(SupplierModel.store_id==store,SupplierModel.supplier_id==b.supplier_id))
   if not ingredient or not supplier:raise ResourceNotFoundError(details={"resource":"ingredient_or_supplier"})
   values={n:convert_quantity(getattr(b,n),b.unit,ingredient.base_unit) for n in ("moq","pack_size")}
   versions=list(s.scalars(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.supplier_id==b.supplier_id,SupplierIngredientTermModel.ingredient_id==b.ingredient_id).order_by(SupplierIngredientTermModel.version)))
   current=next((x for x in versions if x.active),None)
   if kind=="supplier_update":
    current=s.scalar(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.constraint_id==target))
    if not current:raise ResourceNotFoundError(details={"resource":"supplier_constraint"})
    if not current.active:
     latest=s.scalar(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.supplier_id==current.supplier_id,SupplierIngredientTermModel.ingredient_id==current.ingredient_id,SupplierIngredientTermModel.active.is_(True)))
     raise VersionConflictError(details={"expected_version":b.version,"current_version":latest.version if latest else current.version})
    if b.version!=current.version:raise VersionConflictError(details={"expected_version":b.version,"current_version":current.version})
   elif current:raise DuplicateRequestError("Active supplier constraint đã tồn tại.")
   same=current and all(getattr(current,n)==v for n,v in values.items()) and current.unit_cost==b.unit_cost and current.lead_time_days==b.lead_time_days and current.shelf_life_days==b.shelf_life_days
   if same:model=current
   else:
    if current:current.active=False
    model=SupplierIngredientTermModel(constraint_id=str(uuid4()),store_id=store,supplier_id=b.supplier_id,ingredient_id=b.ingredient_id,unit_cost=b.unit_cost,moq=values["moq"],pack_size=values["pack_size"],order_unit=None,lead_time_days=b.lead_time_days,shelf_life_days=b.shelf_life_days,unit=ingredient.base_unit,version=max([x.version for x in versions] or [0])+1,active=True,source="api");s.add(model)
   s.flush();result={"constraint_id":model.constraint_id,"ingredient_id":model.ingredient_id,"supplier_id":model.supplier_id,"supplier":supplier.supplier,"unit_cost":model.unit_cost,"moq":str(model.moq),"pack_size":str(model.pack_size),"order_unit":model.order_unit,"lead_time_days":model.lead_time_days,"shelf_life_days":model.shelf_life_days,"unit":model.unit,"version":model.version,"active":model.active}
   AuditService(AuditLogRepository(s)).record(store_id=store,action=kind,resource_type="supplier_constraint",resource_id=model.constraint_id,after={"version":model.version},source="api")
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=path,http_method="POST" if kind=="supplier_create" else "PUT",idempotency_key=key);rec.resource_type="supplier_constraint";rec.resource_id=model.constraint_id;rec.response_status=201 if kind=="supplier_create" else 200;rec.response_body_json=json.dumps(result,default=str,ensure_ascii=False)
   s.commit();return result
 def _history_batch(self,kind,store,body,key):
  path=f"/api/v1/stores/{store}/"+("sales-history/batch" if kind=="sales_batch" else "purchase-history/batch")
  payload=body.model_dump(mode="json");request_hash=canonical_hash(payload)
  allowed={"pos","manual","integration"} if kind=="sales_batch" else {"supplier_invoice","manual","integration"}
  if body.source not in allowed or (kind=="purchase_batch" and body.inventory_effect!="record_only"):raise ValidationError("source hoặc inventory_effect không hợp lệ.")
  keys=[(r.date,r.product_id,r.promotion) if kind=="sales_batch" else purchase_receipt_identity(store_id=store,source=body.source,external_record_id=r.external_record_id,receipt_date=r.date,ingredient_id=r.ingredient_id,supplier_id=r.supplier_id,quantity=r.quantity,unit=r.unit,unit_cost=r.unit_cost,expiry_date=r.expiry_date,batch_code=r.supplier_lot_code) for r in body.records]
  if len(set(keys))!=len(keys):raise ValidationError("Business key bị lặp trong request.")
  with self.factory() as s:
   StoreRepository(s).get_required(store);idem=IdempotencyService(IdempotencyRepository(s))
   if key:
    replay=idem.register(store_id=store,endpoint=path,http_method="POST",idempotency_key=key,request_hash=request_hash)
    if replay.is_replay:s.rollback();return json.loads(replay.record.response_body_json)
   created=unchanged=0;records=[];warnings=[];sales_dates=set()
   for r in body.records:
    if kind=="sales_batch":
     if not s.scalar(select(ProductModel).where(ProductModel.store_id==store,ProductModel.product_id==r.product_id)):raise ResourceNotFoundError(details={"resource":"product","product_id":r.product_id})
     current=s.scalar(select(SalesDailyModel).where(SalesDailyModel.store_id==store,SalesDailyModel.date==r.date,SalesDailyModel.product_id==r.product_id,SalesDailyModel.promotion==r.promotion))
     same=current and current.quantity==r.quantity and current.unit_price==r.unit_price and current.source==body.source and current.promotion==r.promotion and current.external_record_id==r.external_record_id
     if current and not same:raise DuplicateRequestError("Sales business key đã tồn tại với nội dung khác.")
     if current:unchanged+=1;rid=current.sales_record_id;status="unchanged"
     else:
      rid=str(uuid4());s.add(SalesDailyModel(sales_record_id=rid,store_id=store,date=r.date,product_id=r.product_id,quantity=r.quantity,unit_price=r.unit_price,promotion=r.promotion,source=body.source,external_record_id=r.external_record_id));created+=1;status="created"
     sales_dates.add(r.date)
     records.append({"external_record_id":r.external_record_id,"sales_record_id":rid,"status":status})
    else:
     ingredient=s.scalar(select(IngredientModel).where(IngredientModel.store_id==store,IngredientModel.ingredient_id==r.ingredient_id));supplier=s.scalar(select(SupplierModel).where(SupplierModel.store_id==store,SupplierModel.supplier_id==r.supplier_id))
     if not ingredient or not supplier:raise ResourceNotFoundError(details={"resource":"ingredient_or_supplier"})
     quantity=convert_quantity(r.quantity,r.unit,ingredient.base_unit)
     external=normalized_optional_identifier(r.external_record_id)
     identity_kind, identity_key=purchase_receipt_identity(store_id=store,source=body.source,external_record_id=external,receipt_date=r.date,ingredient_id=r.ingredient_id,supplier_id=r.supplier_id,quantity=quantity,unit=ingredient.base_unit,unit_cost=r.unit_cost,expiry_date=r.expiry_date,batch_code=r.supplier_lot_code)
     current_query=select(PurchaseReceiptModel).where(PurchaseReceiptModel.store_id==store)
     current_query=current_query.where(PurchaseReceiptModel.source==body.source,PurchaseReceiptModel.external_record_id==external) if identity_kind=="external" else current_query.where(PurchaseReceiptModel.business_key_hash==identity_key)
     current=s.scalar(current_query)
     same=current and current.quantity==quantity and current.unit_cost==r.unit_cost and current.receipt_date==r.date and current.ingredient_id==r.ingredient_id and current.supplier_id==r.supplier_id
     if current and not same:raise DuplicateRequestError("Purchase external ID đã tồn tại với nội dung khác.")
     if current:unchanged+=1;rid=current.receipt_id;status="unchanged"
     else:
      rid=str(uuid4());s.add(PurchaseReceiptModel(receipt_id=rid,store_id=store,ingredient_id=r.ingredient_id,supplier_id=r.supplier_id,receipt_date=r.date,quantity=quantity,unit=ingredient.base_unit,unit_cost=r.unit_cost,expiry_date=r.expiry_date,batch_code=normalized_optional_identifier(r.supplier_lot_code),source=body.source,external_record_id=external,business_key_hash=identity_key if identity_kind=="business_hash" else None,inventory_effect="record_only"));created+=1;status="created"
     records.append({"external_record_id":r.external_record_id,"purchase_record_id":rid,"status":status})
   if kind=="sales_batch":warnings.extend(reconcile_usage_from_sales(s,store,sales_dates))
   result={"batch_id":str(uuid4()),"created_count":created,"unchanged_count":unchanged,"records":records}
   if kind=="sales_batch":result.update({"usage_rebuild":{"status":"completed","warning_count":len(warnings)},"warnings":warnings})
   else:result["inventory_applied"]=False
   AuditService(AuditLogRepository(s)).record(store_id=store,action=kind,resource_type="history_batch",resource_id=result["batch_id"],after={"created_count":created,"unchanged_count":unchanged},source="api")
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=path,http_method="POST",idempotency_key=key);rec.response_status=201;rec.response_body_json=json.dumps(result,default=str)
   try:s.commit()
   except IntegrityError as exc:
    s.rollback()
    if "uq_purchase_receipts_external_identity" in str(exc) or "purchase_receipts.store_id, purchase_receipts.source, purchase_receipts.external_record_id" in str(exc):raise BusinessIdentityConflictError("PURCHASE_RECEIPT_DUPLICATE","Purchase receipt external identity already exists.") from None
    if "uq_sales_natural_key" in str(exc) or "sales_daily.store_id, sales_daily.date, sales_daily.product_id, sales_daily.promotion" in str(exc):raise BusinessIdentityConflictError("DUPLICATE_SALES_DAILY_RECORD","Sales daily identity already exists.") from None
    raise
   return result
 def forecast_create(self,store,b,key):
  return self.forecast_service.create_legacy_run(store,b,key)
 def forecast_get(self,store,rid,result):
  return self.forecast_service.get_legacy_result(rid,store) if result else self.forecast_service.get_metadata(rid,store)
 def _plan_status(self,x):
  return {"plan_run_id":x.plan_run_id,"status":x.status,"engine_status":x.engine_status,"forecast_run_id":x.forecast_run_id,"strategy":x.strategy,"budget_limit":x.budget_limit,"as_of_date":x.as_of_date}
 def plan_create(self,store,b,key):
  return self.decision_planning_service.create_legacy_plan(store,b,key)
 def plan_get(self,store,rid,result):
  return self.decision_planning_service.get_legacy_plan_result(store,rid) if result else self.decision_planning_service.get_legacy_plan_metadata(store,rid)
 def _po_public(self,s,po):
  supplier=s.scalar(select(SupplierModel).where(SupplierModel.supplier_id==po.supplier_id))
  lines=list(s.scalars(select(PurchaseOrderLineModel).where(PurchaseOrderLineModel.po_id==po.po_id).order_by(PurchaseOrderLineModel.po_line_id)))
  return {"po_id":po.po_id,"supplier_id":po.supplier_id,"supplier":supplier.supplier if supplier else None,"plan_run_id":po.plan_run_id,
   "order_date":po.order_date,"delivery_date":po.delivery_date,"strategy":po.strategy,"status":po.status,
   "lines":[{"po_line_id":x.po_line_id,"ingredient_id":x.ingredient_id,"order_quantity":str(x.ordered_quantity),
   "received_quantity":str(x.received_quantity),"unit":x.unit,"unit_cost":x.unit_cost,"line_total":x.cost,"shelf_life_days":x.shelf_life_days,
   "projected_expiry_date":resolve_inbound_expiry(arrival_date=po.delivery_date,shelf_life_days=x.shelf_life_days)} for x in lines],
   "total":po.total,"budget_after":po.budget_after,"version":po.version,"confirmed_at":po.confirmed_at,"received_at":po.received_at}
 def _budget(self,s,store,period):
  bp=s.scalar(select(BudgetPeriodModel).where(BudgetPeriodModel.store_id==store,BudgetPeriodModel.period==period))
  if bp:return bp
  settings=s.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id==store))
  bp=BudgetPeriodModel(budget_period_id=str(uuid4()),store_id=store,period=period,monthly_budget=settings.monthly_budget if settings else 0,reserved_budget=0,spent_budget=0)
  s.add(bp);s.flush();return bp
 def po(self,action,store,po_id=None,body=None,key=None):
  path=f"/api/v1/stores/{store}/purchase-orders"+(f"/{po_id}" if po_id else "")+("/receive" if action=="receive" else "")
  with self.factory() as s:
   StoreRepository(s).get_required(store)
   if action=="list":
    rows=list(s.scalars(select(PurchaseOrderModel).where(PurchaseOrderModel.store_id==store).order_by(PurchaseOrderModel.created_at.desc())))
    return {"items":[self._po_public(s,x) for x in rows],"page":1,"page_size":50,"total":len(rows)}
   if action=="get":
    po=s.scalar(select(PurchaseOrderModel).where(PurchaseOrderModel.store_id==store,PurchaseOrderModel.po_id==po_id))
    if not po:raise ResourceNotFoundError(details={"resource":"purchase_order"})
    return self._po_public(s,po)
   payload=body.model_dump(mode="json");idem=IdempotencyService(IdempotencyRepository(s))
   if key:
    replay=idem.register(store_id=store,endpoint=path,http_method="POST",idempotency_key=key,request_hash=canonical_hash(payload))
    if replay.is_replay:s.rollback();return json.loads(replay.record.response_body_json)
   if action=="create":
    plan=s.scalar(select(PlanRunModel).where(PlanRunModel.store_id==store,PlanRunModel.plan_run_id==body.plan_run_id,PlanRunModel.status=="completed"))
    if not plan:raise ResourceNotFoundError("Completed plan not found.",{"resource":"plan_run"})
    if plan.engine_status=="model_not_ready":raise ValidationError("Không thể tạo purchase order từ mock fallback.",{"reason":"model_not_ready"})
    if len({x.recommendation_id for x in body.lines})!=len(body.lines):raise ValidationError("recommendation_id bị lặp.")
    grouped={}
    for request_line in body.lines:
     rec=s.scalar(select(RecommendationModel).where(RecommendationModel.store_id==store,RecommendationModel.plan_run_id==plan.plan_run_id,RecommendationModel.recommendation_id==request_line.recommendation_id))
     if not rec:raise ResourceNotFoundError(details={"resource":"recommendation"})
     qty=Decimal(request_line.order_quantity_override) if request_line.order_quantity_override is not None else Decimal(rec.order_quantity)
     if qty<=0 or qty<Decimal(rec.moq) or qty%Decimal(rec.pack_size)!=0:raise ValidationError("Số lượng PO không thỏa MOQ/pack_size.")
     grouped.setdefault(rec.supplier_id,[]).append((rec,qty))
    bp=self._budget(s,store,date.today().strftime("%Y-%m"));remaining=bp.monthly_budget-bp.reserved_budget-bp.spent_budget;orders=[]
    for supplier_id,lines in grouped.items():
     total=sum(int(q*Decimal(x.unit_cost)) for x,q in lines)
     po=PurchaseOrderModel(po_id=str(uuid4()),store_id=store,plan_run_id=plan.plan_run_id,supplier_id=supplier_id,order_date=date.today(),delivery_date=date.today()+timedelta(days=max(x.lead_time_days for x,_ in lines)),strategy=plan.strategy,status="draft",total=total,budget_after=remaining-total,version=1)
     s.add(po);s.flush()
     for rec,qty in lines:
      term=s.scalar(select(SupplierIngredientTermModel).where(SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.supplier_id==rec.supplier_id,SupplierIngredientTermModel.ingredient_id==rec.ingredient_id,SupplierIngredientTermModel.active.is_(True)))
      s.add(PurchaseOrderLineModel(po_line_id=str(uuid4()),po_id=po.po_id,recommendation_id=rec.recommendation_id,ingredient_id=rec.ingredient_id,ordered_quantity=qty,received_quantity=0,unit=rec.unit,unit_cost=rec.unit_cost,cost=int(qty*Decimal(rec.unit_cost)),moq=rec.moq,pack_size=rec.pack_size,shelf_life_days=term.shelf_life_days if term else None,version=1))
     s.flush();orders.append(self._po_public(s,po))
    result={"orders":orders}
   else:
    po=s.scalar(select(PurchaseOrderModel).where(PurchaseOrderModel.store_id==store,PurchaseOrderModel.po_id==po_id))
    if not po:raise ResourceNotFoundError(details={"resource":"purchase_order"})
    if body.version!=po.version:raise VersionConflictError(details={"expected_version":body.version,"current_version":po.version})
    lines={x.po_line_id:x for x in s.scalars(select(PurchaseOrderLineModel).where(PurchaseOrderLineModel.po_id==po.po_id))}
    if action=="patch":
     if po.status!="draft":raise InvalidStateTransitionError()
     if len({x.po_line_id for x in body.line_updates})!=len(body.line_updates):raise ValidationError("po_line_id bị lặp.")
     for update in body.line_updates:
      line=lines.get(update.po_line_id)
      if not line:raise ResourceNotFoundError(details={"resource":"purchase_order_line"})
      qty=Decimal(update.order_quantity)
      if qty<Decimal(line.moq) or qty%Decimal(line.pack_size)!=0:raise ValidationError("Số lượng PO không thỏa MOQ/pack_size.")
      line.ordered_quantity=qty;line.cost=int(qty*Decimal(line.unit_cost));line.version+=1
     po.total=sum(x.cost for x in lines.values());po.version+=1;bp=self._budget(s,store,date.today().strftime("%Y-%m"));po.budget_after=bp.monthly_budget-bp.reserved_budget-bp.spent_budget-po.total
    elif action=="confirm":
     if po.status!="draft":raise InvalidStateTransitionError()
     if body.confirmed_at.tzinfo is None:raise ValidationError("confirmed_at phải có timezone.")
     bp=self._budget(s,store,body.confirmed_at.date().strftime("%Y-%m"));po.total=sum(x.cost for x in lines.values());remaining=bp.monthly_budget-bp.reserved_budget-bp.spent_budget
     if po.total>remaining:raise BudgetExceededError(details={"remaining_budget":remaining,"po_total":po.total})
     bp.reserved_budget+=po.total;po.status="ordered";po.confirmed_at=body.confirmed_at;po.budget_after=remaining-po.total;po.version+=1
    elif action=="receive":
     if po.status not in {"ordered","partially_received"}:raise InvalidStateTransitionError()
     if body.received_at.tzinfo is None:raise ValidationError("received_at phải có timezone.")
     if po.confirmed_at and body.received_at.replace(tzinfo=None)<po.confirmed_at.replace(tzinfo=None):raise ValidationError("received_at trước confirmed_at.")
     if len({x.po_line_id for x in body.lines})!=len(body.lines):raise ValidationError("po_line_id bị lặp.")
     bp=self._budget(s,store,po.confirmed_at.date().strftime("%Y-%m"));received_cost=0;receipt_date=body.received_at.date()
     for received_line in body.lines:
      line=lines.get(received_line.po_line_id)
      if not line:raise ResourceNotFoundError(details={"resource":"purchase_order_line"})
      total_qty=sum((Decimal(x.quantity) for x in received_line.lots),Decimal(0))
      if Decimal(line.received_quantity)+total_qty>Decimal(line.ordered_quantity):raise ValidationError("Không được nhận vượt số lượng đặt.")
      for lot_in in received_line.lots:
       if lot_in.expiry_date and lot_in.expiry_date<receipt_date:raise ValidationError("expiry_date trước ngày nhận.")
       qty=Decimal(lot_in.quantity);batch_code=normalized_optional_identifier(lot_in.supplier_lot_code)
       lot=None
       if batch_code is not None:
        lot=s.scalar(select(InventoryLotModel).where(InventoryLotModel.store_id==store,InventoryLotModel.ingredient_id==line.ingredient_id,InventoryLotModel.batch_code==batch_code))
       if lot is None:
        lot_id=str(uuid4());lot=InventoryLotModel(lot_id=lot_id,store_id=store,ingredient_id=line.ingredient_id,supplier_id=po.supplier_id,batch_code=batch_code,received_date=receipt_date,expiry_date=lot_in.expiry_date,initial_quantity=qty,unit=line.unit,unit_cost=line.unit_cost,source="purchase_order",version=1);s.add(lot);s.flush()
       else:
        conflicts=[]
        if lot.unit!=line.unit:conflicts.append("unit")
        if lot.expiry_date!=lot_in.expiry_date:conflicts.append("expiry_date")
        if lot.supplier_id is not None and lot.supplier_id!=po.supplier_id:conflicts.append("supplier_id")
        if conflicts:raise BusinessIdentityConflictError("INVENTORY_LOT_METADATA_CONFLICT","Batch lot metadata conflicts with the existing lot.",{"store_id":store,"ingredient_id":line.ingredient_id,"batch_code":batch_code,"existing":{"supplier_id":lot.supplier_id,"unit":lot.unit,"expiry_date":lot.expiry_date},"incoming":{"supplier_id":po.supplier_id,"unit":line.unit,"expiry_date":lot_in.expiry_date},"conflicting_fields":conflicts})
        lot_id=lot.lot_id
       s.add(InventoryMovementModel(movement_id=str(uuid4()),store_id=store,lot_id=lot_id,movement_type="receipt",quantity_delta=qty,unit=line.unit,occurred_at=body.received_at,source="purchase_order",source_id=po.po_id))
       s.add(PurchaseReceiptModel(receipt_id=str(uuid4()),store_id=store,ingredient_id=line.ingredient_id,supplier_id=po.supplier_id,receipt_date=receipt_date,quantity=qty,unit=line.unit,unit_cost=line.unit_cost,expiry_date=lot_in.expiry_date,batch_code=batch_code,source="purchase_order",external_record_id=f"{body.delivery_reference}:{line.po_line_id}:{lot_id}",inventory_effect="applied",po_id=po.po_id,po_line_id=line.po_line_id))
      line.received_quantity=Decimal(line.received_quantity)+total_qty;line.version+=1;received_cost+=int(total_qty*Decimal(line.unit_cost))
     if received_cost>bp.reserved_budget:raise ValidationError("Budget reservation không đủ.")
     bp.reserved_budget-=received_cost;bp.spent_budget+=received_cost
     complete=all(Decimal(x.received_quantity)==Decimal(x.ordered_quantity) for x in lines.values())
     po.status="received" if complete else "partially_received";po.received_at=body.received_at if complete else None;po.version+=1
    else:raise ValidationError("Unknown PO action.")
   if action != "create":
    s.flush();result=self._po_public(s,po)
   AuditService(AuditLogRepository(s)).record(store_id=store,action=f"purchase_order_{action}",resource_type="purchase_order",resource_id=po_id or ",".join(x["po_id"] for x in result["orders"]),after={"action":action},source="api")
   if key:
    rec=IdempotencyRepository(s).get(store_id=store,endpoint=path,http_method="POST",idempotency_key=key);rec.response_status=201;rec.response_body_json=json.dumps(result,default=str,ensure_ascii=False)
   try:s.commit()
   except IntegrityError as exc:
    s.rollback()
    if "uq_purchase_receipts_external_identity" in str(exc) or "purchase_receipts.store_id, purchase_receipts.source, purchase_receipts.external_record_id" in str(exc):raise BusinessIdentityConflictError("PURCHASE_RECEIPT_DUPLICATE","Purchase receipt external identity already exists.") from None
    if "uq_inventory_lots_store_ingredient_batch_present" in str(exc) or "inventory_lots.store_id, inventory_lots.ingredient_id, inventory_lots.batch_code" in str(exc):raise BusinessIdentityConflictError("INVENTORY_LOT_METADATA_CONFLICT","Inventory lot batch identity already exists.") from None
    raise
   return result
