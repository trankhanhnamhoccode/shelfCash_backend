import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.business_constraints import validate_and_normalize_business_constraint
from app.core.exceptions import PlanningError, ResourceNotFoundError, ValidationError, VersionConflictError
from app.core.provenance import canonical_hash
from app.models.business import IngredientModel, InventoryConstraintModel
from app.models.operations import ForecastRunModel
from app.models.planning import ProcurementPlanModel, ProcurementPlanRunModel
from app.repositories.audit_logs import AuditLogRepository
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.stores import StoreRepository
from app.services.audit_service import AuditService
from app.services.idempotency_service import IdempotencyService


class InventoryConstraintWriteService:
    def __init__(self,factory):self.factory=factory

    def create(self,store,body,key):
        path=f"/api/v1/stores/{store}/inventory-constraints";payload=body.model_dump(mode="json")
        with self.factory() as session:
            StoreRepository(session).get_required(store)
            replay=self._idempotency(session,store,path,"POST",key,payload)
            if replay is not None:return replay
            kind,ingredient,normalized=self._normalize(session,store,body.ingredient_id,body.constraint_type,
                body.value,body.unit,body.currency)
            latest=self._latest(session,store,ingredient.ingredient_id if ingredient else None,kind)
            if latest is not None:
                raise VersionConflictError(details={"reason":"CONSTRAINT_FAMILY_EXISTS","constraint_id":latest.constraint_id,
                    "current_version":latest.version,"use":"PATCH"})
            model=InventoryConstraintModel(constraint_id=str(uuid4()),store_id=store,
                ingredient_id=ingredient.ingredient_id if ingredient else None,constraint_type=kind,value=normalized.value,
                unit=normalized.unit,effective_date=body.effective_date,end_date=None,version=1,active=True,
                source="api",note=body.note,currency=normalized.unit if normalized.dimension=="currency" else None)
            session.add(model);session.flush();result=self._response(session,model)
            self._audit(session,store,"inventory_constraint_created",model,None,result["constraint"])
            self._save_idempotency(session,store,path,"POST",key,result,model.constraint_id);session.commit();return result

    def update(self,store,constraint_id,body,key):
        path=f"/api/v1/stores/{store}/inventory-constraints/{constraint_id}";payload=body.model_dump(mode="json")
        with self.factory() as session:
            StoreRepository(session).get_required(store);replay=self._idempotency(session,store,path,"PATCH",key,payload)
            if replay is not None:return replay
            current=self._required(session,store,constraint_id)
            latest=self._latest(session,store,current.ingredient_id,current.constraint_type)
            if latest.constraint_id!=current.constraint_id or current.version!=body.expected_version:
                raise VersionConflictError(details={"expected_version":body.expected_version,"current_version":latest.version,
                    "current_constraint_id":latest.constraint_id})
            effective=body.effective_date or current.effective_date
            same_date=effective==current.effective_date
            if same_date and body.correction_mode!="replace_same_effective_date":
                raise ValidationError("Same-date update requires explicit correction mode.",{"required_correction_mode":"replace_same_effective_date"})
            if effective<current.effective_date:
                raise ValidationError("New effective_date cannot precede the current version.")
            if same_date and self._used_by_completed_plan(session,current):
                raise PlanningError("BUSINESS_CONSTRAINT_CORRECTION_BLOCKED","Constraint was used by a completed planning run.",{
                    "constraint_id":current.constraint_id,"version":current.version},http_status=409)
            _,ingredient,normalized=self._normalize(session,store,current.ingredient_id,current.constraint_type,
                body.value,body.unit,body.currency)
            new=InventoryConstraintModel(constraint_id=str(uuid4()),store_id=store,ingredient_id=current.ingredient_id,
                constraint_type=current.constraint_type,value=normalized.value,unit=normalized.unit,effective_date=effective,
                end_date=None,version=current.version+1,active=True,source="api",
                note=body.note if "note" in body.model_fields_set else current.note,
                currency=normalized.unit if normalized.dimension=="currency" else None)
            session.add(new);session.flush();before=self._item(current)
            current.active=False
            if same_date:current.superseded_by_constraint_id=new.constraint_id
            else:current.end_date=effective-timedelta(days=1)
            result=self._response(session,new)
            self._audit(session,store,"inventory_constraint_corrected" if same_date else "inventory_constraint_version_created",
                new,before,result["constraint"])
            self._save_idempotency(session,store,path,"PATCH",key,result,new.constraint_id)
            try:session.commit()
            except IntegrityError as exc:
                session.rollback();raise VersionConflictError(details={"expected_version":body.expected_version}) from exc
            return result

    def deactivate(self,store,constraint_id,body,key):
        path=f"/api/v1/stores/{store}/inventory-constraints/{constraint_id}/deactivate";payload=body.model_dump(mode="json")
        with self.factory() as session:
            StoreRepository(session).get_required(store);replay=self._idempotency(session,store,path,"POST",key,payload)
            if replay is not None:return replay
            current=self._required(session,store,constraint_id);latest=self._latest(session,store,current.ingredient_id,current.constraint_type)
            if latest.constraint_id!=current.constraint_id or current.version!=body.expected_version:
                raise VersionConflictError(details={"expected_version":body.expected_version,"current_version":latest.version})
            if body.end_date<current.effective_date:raise ValidationError("end_date cannot precede effective_date.")
            before=self._item(current);current.active=False;current.end_date=body.end_date
            if body.note is not None:current.note=body.note
            session.flush()
            result=self._response(session,current);self._audit(session,store,"inventory_constraint_deactivated",current,before,result["constraint"])
            self._save_idempotency(session,store,path,"POST",key,result,current.constraint_id);session.commit();return result

    def _normalize(self,session,store,ingredient_id,constraint_type,value,unit,currency):
        ingredient=None
        if ingredient_id is not None:
            ingredient=session.scalar(select(IngredientModel).where(IngredientModel.store_id==store,IngredientModel.ingredient_id==ingredient_id))
            if ingredient is None:raise ResourceNotFoundError(details={"resource":"ingredient","ingredient_id":ingredient_id})
        if currency is not None and unit is not None and currency.upper()!=unit.upper():
            raise ValidationError("unit and currency conflict.",{"unit":unit,"currency":currency})
        if currency is not None and constraint_type not in {"budget","store_budget"}:
            raise ValidationError("currency is only valid for currency constraints.",{"constraint_type":constraint_type})
        raw_unit=currency or unit
        normalized=validate_and_normalize_business_constraint(constraint_type,value,raw_unit,ingredient)
        return normalized.constraint_type,ingredient,normalized

    @staticmethod
    def _latest(session,store,ingredient,kind):
        return session.scalar(select(InventoryConstraintModel).where(InventoryConstraintModel.store_id==store,
            InventoryConstraintModel.ingredient_id==ingredient,InventoryConstraintModel.constraint_type==kind)
            .order_by(InventoryConstraintModel.version.desc()))
    @staticmethod
    def _required(session,store,constraint_id):
        item=session.scalar(select(InventoryConstraintModel).where(InventoryConstraintModel.store_id==store,
            InventoryConstraintModel.constraint_id==constraint_id))
        if item is None:raise ResourceNotFoundError(details={"resource":"inventory_constraint","constraint_id":constraint_id})
        return item
    @staticmethod
    def _used_by_completed_plan(session,constraint):
        rows=session.execute(select(ProcurementPlanModel,ForecastRunModel).join(ProcurementPlanRunModel,
            ProcurementPlanRunModel.procurement_plan_run_id==ProcurementPlanModel.procurement_plan_run_id).join(
            ForecastRunModel,ForecastRunModel.forecast_run_id==ProcurementPlanRunModel.forecast_run_id).where(
            ProcurementPlanRunModel.store_id==constraint.store_id,ProcurementPlanRunModel.status=="completed",
            ForecastRunModel.cutoff_date>=constraint.effective_date)).all()
        for plan,forecast in rows:
            if constraint.end_date and forecast.cutoff_date>constraint.end_date:continue
            if constraint.ingredient_id is None:return True
            metrics=json.loads(plan.metrics_json or "{}")
            if constraint.ingredient_id in metrics.get("constraint_trace",{}):return True
        return False
    def _response(self,session,current):
        history=list(session.scalars(select(InventoryConstraintModel).where(InventoryConstraintModel.store_id==current.store_id,
            InventoryConstraintModel.ingredient_id==current.ingredient_id,InventoryConstraintModel.constraint_type==current.constraint_type)
            .order_by(InventoryConstraintModel.version.desc())))
        return {"constraint":self._item(current),"history":[{"constraint_id":x.constraint_id,"version":x.version,
            "effective_date":x.effective_date,"end_date":x.end_date,"active":x.active,
            "superseded_by_constraint_id":x.superseded_by_constraint_id} for x in history]}
    @staticmethod
    def _item(x):
        return {"constraint_id":x.constraint_id,"store_id":x.store_id,"ingredient_id":x.ingredient_id,
            "constraint_type":x.constraint_type,"value":str(x.value),"unit":x.unit,"currency":x.currency,
            "effective_date":x.effective_date,"end_date":x.end_date,"version":x.version,"active":x.active,"note":x.note,
            "superseded_by_constraint_id":x.superseded_by_constraint_id}
    @staticmethod
    def _audit(session,store,action,model,before,after):
        AuditService(AuditLogRepository(session)).record(store_id=store,action=action,resource_type="inventory_constraint",
            resource_id=model.constraint_id,before=before,after=after,source="api")
    @staticmethod
    def _idempotency(session,store,path,method,key,payload):
        if not key:return None
        replay=IdempotencyService(IdempotencyRepository(session)).register(store_id=store,endpoint=path,http_method=method,
            idempotency_key=key,request_hash=canonical_hash(payload))
        if replay.is_replay:
            result=json.loads(replay.record.response_body_json);session.rollback();return result
        return None
    @staticmethod
    def _save_idempotency(session,store,path,method,key,result,resource_id):
        if not key:return
        record=IdempotencyRepository(session).get(store_id=store,endpoint=path,http_method=method,idempotency_key=key)
        record.resource_type="inventory_constraint";record.resource_id=resource_id;record.response_status=200
        record.response_body_json=json.dumps(result,default=str,ensure_ascii=False)
