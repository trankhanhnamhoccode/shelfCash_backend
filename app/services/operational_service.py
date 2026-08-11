from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import func, select

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.models.business import *
from app.models.import_normalized import ImportFileModel, ImportIssueModel, ImportJobModel, ImportSheetProfileModel
from app.models.operations import BudgetPeriodModel
from app.repositories.stores import StoreRepository


class OperationalService:
    def __init__(self, factory): self.factory = factory
    @staticmethod
    def _page(session, stmt, page, size):
        total = session.scalar(select(func.count()).select_from(stmt.subquery()))
        return list(session.scalars(stmt.offset((page-1)*size).limit(size))), total
    @staticmethod
    def _wrap(items, page, size, total): return {"items": items, "page": page, "page_size": size, "total": total}
    def imports(self, store, page, size, status=None, start=None, end=None):
        with self.factory() as s:
            StoreRepository(s).get_required(store); q=select(ImportJobModel).where(ImportJobModel.store_id==store)
            if status: q=q.where(ImportJobModel.status==status)
            if start: q=q.where(func.date(ImportJobModel.created_at)>=start)
            if end: q=q.where(func.date(ImportJobModel.created_at)<=end)
            rows,total=self._page(s,q.order_by(ImportJobModel.created_at.desc(),ImportJobModel.import_id),page,size)
            out=[]
            for x in rows:
                out.append({"import_id":x.import_id,"store_id":x.store_id,"status":x.legacy_status or x.status,"forecast_date":x.forecast_date,"forecast_horizon":x.forecast_horizon,"requires_review":x.requires_review,"file_count":s.scalar(select(func.count()).select_from(ImportFileModel).where(ImportFileModel.import_id==x.import_id)),"profile_count":s.scalar(select(func.count()).select_from(ImportSheetProfileModel).where(ImportSheetProfileModel.import_id==x.import_id)),"warning_count":s.scalar(select(func.count()).select_from(ImportIssueModel).where(ImportIssueModel.import_id==x.import_id,ImportIssueModel.severity=="warning")),"error_count":s.scalar(select(func.count()).select_from(ImportIssueModel).where(ImportIssueModel.import_id==x.import_id,ImportIssueModel.severity=="error")),"created_at":x.created_at,"completed_at":x.completed_at,"failed_at":x.failed_at})
            return self._wrap(out,page,size,total)
    def recipe_versions(self,store,product,page,size):
        with self.factory() as s:
            StoreRepository(s).get_required(store)
            target=s.scalar(select(ProductModel).where(ProductModel.store_id==store,ProductModel.product_id==product))
            if target and target.item_type=="combo":
                from app.core.exceptions import MenuError
                raise MenuError("RECIPE_NOT_ALLOWED_FOR_COMBO","Combo sử dụng components; không lưu recipe trực tiếp.",{"product_id":product},http_status=409)
            if not s.scalar(select(ProductModel).where(ProductModel.store_id==store,ProductModel.product_id==product)): raise ResourceNotFoundError(details={"resource":"product"})
            q=select(RecipeVersionModel).where(RecipeVersionModel.store_id==store,RecipeVersionModel.product_id==product).order_by(RecipeVersionModel.version.desc())
            rows,total=self._page(s,q,page,size)
            items=[]
            for v in rows:
                lines=s.execute(select(RecipeLineModel,IngredientModel).join(IngredientModel,IngredientModel.ingredient_id==RecipeLineModel.ingredient_id).where(RecipeLineModel.recipe_version_id==v.recipe_version_id))
                items.append({"recipe_version_id":v.recipe_version_id,"version":v.version,"effective_from":v.effective_from,"effective_to":v.effective_to,"content_hash":v.content_hash,"lines":[{"recipe_line_id":l.recipe_line_id,"ingredient_id":i.ingredient_id,"ingredient":i.ingredient,"quantity":str(l.quantity),"unit":l.unit} for l,i in lines],"created_at":v.created_at})
            return self._wrap(items,page,size,total)
    def history(self,kind,store,page,size,start=None,end=None,entity=None,source=None,supplier=None):
        if start and end and start > end: raise ValidationError("date_from phải nhỏ hơn hoặc bằng date_to.")
        model,day={"sales":(SalesDailyModel,SalesDailyModel.date),"usage":(UsageDailyModel,UsageDailyModel.date),"purchase":(PurchaseReceiptModel,PurchaseReceiptModel.receipt_date)}[kind]
        with self.factory() as s:
            StoreRepository(s).get_required(store); q=select(model).where(model.store_id==store)
            if start:q=q.where(day>=start)
            if end:q=q.where(day<=end)
            if source:q=q.where(model.source==source)
            field=model.product_id if kind=="sales" else model.ingredient_id
            if entity:q=q.where(field==entity)
            if supplier and kind=="purchase":q=q.where(model.supplier_id==supplier)
            rows,total=self._page(s,q.order_by(day.desc()),page,size)
            return self._wrap([self._public(x) for x in rows],page,size,total)
    @staticmethod
    def _public(x): return {c.name:getattr(x,c.name) for c in x.__table__.columns if c.name not in {"source_row_hash","natural_key_hash","business_key_hash","profile_id"}}
    def inventory(self,store,page,size,ingredient=None):
        with self.factory() as s:
            StoreRepository(s).get_required(store);q=select(InventoryLotModel).where(InventoryLotModel.store_id==store)
            if ingredient:q=q.where(InventoryLotModel.ingredient_id==ingredient)
            rows,total=self._page(s,q.order_by(InventoryLotModel.expiry_date),page,size);items=[]
            for x in rows:
                bal=s.scalar(select(func.coalesce(func.sum(InventoryMovementModel.quantity_delta),0)).where(InventoryMovementModel.lot_id==x.lot_id))
                ing=s.scalar(select(IngredientModel).where(IngredientModel.store_id==store,IngredientModel.ingredient_id==x.ingredient_id))
                sup=s.scalar(select(SupplierModel).where(SupplierModel.store_id==store,SupplierModel.supplier_id==x.supplier_id)) if x.supplier_id else None
                today = date.today()
                expired = bal if bal > 0 and x.expiry_date and x.expiry_date < today else Decimal("0")
                expiring = bal if bal > 0 and x.expiry_date and today <= x.expiry_date <= today + timedelta(days=7) else Decimal("0")
                status = "stockout" if bal <= 0 else ("expired" if expired else ("expiring" if expiring else "healthy"))
                items.append({"lot_id":x.lot_id,"ingredient_id":x.ingredient_id,"ingredient":ing.ingredient if ing else None,
                    "sku":ing.sku if ing else None,"on_hand":bal,"usable_quantity":max(Decimal(0),bal-expiring-expired),
                    "expiring_quantity":expiring,"expired_quantity":expired,"unit":x.unit,"unit_cost":x.unit_cost,"batch_code":x.batch_code,"received_date":x.received_date,
                    "expiry_date":x.expiry_date,"supplier_id":x.supplier_id,"supplier":sup.supplier if sup else None,
                    "status":status,"last_counted_at":x.last_counted_at,"version":x.version})
            return self._wrap(items,page,size,total)
    def movements(self,store,page,size,lot=None,ingredient=None,mtype=None):
        with self.factory() as s:
            StoreRepository(s).get_required(store);q=select(InventoryMovementModel).join(InventoryLotModel).where(InventoryMovementModel.store_id==store)
            if lot:q=q.where(InventoryMovementModel.lot_id==lot)
            if ingredient:q=q.where(InventoryLotModel.ingredient_id==ingredient)
            if mtype:q=q.where(InventoryMovementModel.movement_type==mtype)
            rows,total=self._page(s,q.order_by(InventoryMovementModel.occurred_at.desc()),page,size);return self._wrap([self._public(x) for x in rows],page,size,total)
    def settings(self,store):
        with self.factory() as s:
            StoreRepository(s).get_required(store);x=s.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id==store))
            bp=s.scalar(select(BudgetPeriodModel).where(BudgetPeriodModel.store_id==store,BudgetPeriodModel.period==date.today().strftime("%Y-%m")))
            monthly=bp.monthly_budget if bp else (x.monthly_budget if x else 0);reserved=bp.reserved_budget if bp else 0;spent=bp.spent_budget if bp else 0
            return {"monthly_budget":monthly,"reserved_budget":reserved,"spent_budget":spent,"remaining_budget":monthly-reserved-spent,"forecast_horizon":x.forecast_horizon if x else 7,"default_strategy":x.default_strategy if x else "balanced","safety_policy":None,"version":x.version if x else 1,"updated_at":x.updated_at if x else None}
    def calendar(self,store,page,size,start=None,end=None):
        if start and end and start > end: raise ValidationError("date_from phải nhỏ hơn hoặc bằng date_to.")
        with self.factory() as s:
            StoreRepository(s).get_required(store);q=select(CalendarFeatureModel).where(CalendarFeatureModel.store_id==store)
            if start:q=q.where(CalendarFeatureModel.date>=start)
            if end:q=q.where(CalendarFeatureModel.date<=end)
            rows,total=self._page(s,q.order_by(CalendarFeatureModel.date),page,size)
            items=[{"date":x.date,"weekday":x.date.strftime("%A"),"weekend":x.date.weekday()>=5,
                    "holiday":x.is_holiday,"promotion":x.is_promotion,
                    "promotion_note":x.promotion_name if x.is_promotion else None} for x in rows]
            return self._wrap(items,page,size,total)
