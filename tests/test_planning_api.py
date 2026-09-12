import json
from datetime import date,datetime,timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import PlanningError
from app.models.audit_log import AuditLogModel
from app.models.business import IngredientModel,ProductModel,RecipeLineModel,RecipeVersionModel,SupplierIngredientTermModel,SupplierModel
from app.models.idempotency import IdempotencyRecordModel
from app.models.operations import ForecastPredictionModel,ForecastRunModel,PlanRunModel,RecommendationModel
from app.models.planning import IngredientDemandPredictionModel,IngredientDemandRunModel,ProcurementPlanRunModel
from app.schemas.planning import IngredientDemandRunResponse,ProcurementPlanRunResponse,ProcurementPlansRequest


def _forecast(session,run_id="planning-forecast",scope=None,status="completed"):
    product=ProductModel(product_id=f"product-{run_id}",store_id="STORE_001",product="Product",normalized_name=f"product-{run_id}",active=True,source="test")
    session.add(product);session.flush()
    run=ForecastRunModel(forecast_run_id=run_id,store_id="STORE_001",cutoff_date=date(2026,8,3),horizon_days=1,
        quantiles_json="[0.25,0.5,0.75]",scope_json='{"ingredient_ids":'+str(scope or []).replace("'",'"')+'}',use_latest_calendar=True,status=status,
        engine_status="forecast_core",request_hash=run_id,model_version="test",warnings_json="[]",created_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc) if status=="completed" else None)
    session.add(run);session.flush()
    session.add(ForecastPredictionModel(prediction_id=str(uuid4()),forecast_run_id=run_id,store_id="STORE_001",product_id=product.product_id,product_name="Product",target_date=date(2026,8,4),horizon=1,p25=10,p50=20,p75=30,interval_lower=9,interval_upper=31,baseline_p50=20,calibration_source="test",warnings_json="[]",created_at=datetime.now(timezone.utc)))
    session.commit();return product


def _procurement_input(session,run_id,target_date=date(2026,8,4)):
    _forecast(session,run_id)
    ingredient_id=f"ingredient-{run_id}"
    session.add(IngredientModel(ingredient_id=ingredient_id,store_id="STORE_001",ingredient="Procurement ingredient",normalized_name=ingredient_id,base_unit="kg",active=True,source="test"))
    session.add(SupplierModel(supplier_id=f"supplier-{run_id}",store_id="STORE_001",supplier="Procurement supplier",normalized_name=f"supplier-{run_id}",active=True,source="test"));session.flush()
    session.add(SupplierIngredientTermModel(constraint_id=f"term-{run_id}",store_id="STORE_001",supplier_id=f"supplier-{run_id}",ingredient_id=ingredient_id,unit_cost=10,moq=Decimal("10"),pack_size=Decimal("5"),lead_time_days=0,unit="kg",version=1,active=True,source="test"))
    demand_id=f"demand-{run_id}";created=datetime.now(timezone.utc)
    session.add(IngredientDemandRunModel(ingredient_demand_run_id=demand_id,forecast_run_id=run_id,store_id="STORE_001",status="completed",warnings_json="[]",created_at=created,completed_at=created));session.flush()
    session.add(IngredientDemandPredictionModel(ingredient_demand_prediction_id=f"prediction-{run_id}",ingredient_demand_run_id=demand_id,forecast_run_id=run_id,store_id="STORE_001",ingredient_id=ingredient_id,ingredient_name="Procurement ingredient",target_date=target_date,horizon=1,unit="kg",p25=3,p50=7,p75=13,source_product_count=1,contributions_json="[]",warnings_json="[]",created_at=created))
    session.commit()


def test_missing_recipe_blocks_demand_without_failing_forecast(client):
    sf=client.app.state.session_factory
    with sf() as s:_forecast(s)
    path="/api/v1/stores/STORE_001/forecast-runs/planning-forecast/ingredient-demand"
    headers={"Idempotency-Key":"blocked-demand"}
    response=client.post(path,headers=headers)
    assert response.status_code==422 and response.json()["code"]=="RECIPE_NOT_FOUND"
    with sf() as s:
        assert s.get(ForecastRunModel,"planning-forecast").status=="completed"
        assert s.scalar(select(IngredientDemandRunModel)).status=="blocked"
        record=s.scalar(select(IdempotencyRecordModel).where(IdempotencyRecordModel.idempotency_key=="blocked-demand"))
        assert record is not None
        # Demand's failed/blocked key is intentionally not resource-linked: replay is by forecast path.
        assert record.resource_type is None and record.resource_id is None and record.response_status is None
    persisted=client.get(path)
    assert persisted.status_code==200 and persisted.json()["status"]=="blocked"
    assert persisted.json()["failure_code"]=="RECIPE_NOT_FOUND" and persisted.json()["predictions"]==[]
    assert IngredientDemandRunResponse.model_validate(persisted.json()).completed_at is not None
    replay=client.post(path,headers=headers)
    assert replay.status_code==200 and replay.json()==persisted.json()


def test_ingredient_demand_unexpected_failure_rolls_back_uncommitted_lifecycle(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:_forecast(s,"unexpected-demand")
    def fail_unexpectedly(_self,*_args,**_kwargs):
        raise RuntimeError("characterized unexpected BOM failure")

    monkeypatch.setattr("app.services.decision.ingredient_demand_lifecycle.CoreBomAdapter.expand",fail_unexpectedly)
    with pytest.raises(RuntimeError,match="characterized unexpected BOM failure"):
        client.app.state.decision_planning_service.generate_demand(
            "STORE_001", "unexpected-demand", "unexpected-demand-key",
        )
    with sf() as s:
        assert s.scalar(select(IngredientDemandRunModel).where(
            IngredientDemandRunModel.forecast_run_id=="unexpected-demand",
        )) is None
        assert s.scalar(select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.idempotency_key=="unexpected-demand-key",
        )) is None


def test_ingredient_demand_typed_response_contract_and_openapi(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:
        product=_forecast(s,"typed-demand")
        ingredient=IngredientModel(ingredient_id="typed-demand-ingredient",store_id="STORE_001",ingredient="Typed ingredient",normalized_name="typed-demand-ingredient",base_unit="kg",active=True,source="test")
        s.add(ingredient)
        recipe=RecipeVersionModel(recipe_version_id="typed-demand-recipe",store_id="STORE_001",product_id=product.product_id,version=1,effective_from=date(2026,1,1),content_hash="typed-demand",source="test",yield_quantity=1,process_loss_rate=0)
        s.add(recipe);s.flush()
        s.add(RecipeLineModel(recipe_line_id="typed-demand-line",recipe_version_id=recipe.recipe_version_id,ingredient_id=ingredient.ingredient_id,quantity=1,unit="kg"))
        s.commit()
    path="/api/v1/stores/STORE_001/forecast-runs/typed-demand/ingredient-demand"
    monkeypatch.setattr(
        "shelfcash_forecast.bom.engine.propagate_ingredient_demand",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shadow BOM must not run in production")),
    )
    created=client.post(path,headers={"Idempotency-Key":"typed-demand"})
    assert created.status_code==200,created.text
    payload=created.json()
    assert IngredientDemandRunResponse.model_validate(payload).model_dump(mode="json")==payload
    assert payload["status"]=="completed" and [row["ingredient_id"] for row in payload["predictions"]]==["typed-demand-ingredient"]
    prediction=payload["predictions"][0]
    assert {"ingredient_id","ingredient_name","target_date","horizon","unit","p25","p50","p75","source_product_count","contributions","warnings"} <= set(prediction)
    assert all(isinstance(prediction[key],float) for key in ("p25","p50","p75"))
    contribution=prediction["contributions"][0]
    assert contribution["recipe_line_id"] == "typed-demand-line"
    assert all(isinstance(contribution[key],str) for key in ("product_p25","product_p50","product_p75","ingredient_p25","ingredient_p50","ingredient_p75"))
    fetched=client.get(path)
    assert fetched.status_code==200 and fetched.json()==payload
    replay=client.post(path,headers={"Idempotency-Key":"typed-demand"})
    assert replay.status_code==200 and replay.json()==payload
    with sf() as s:
        record=s.scalar(select(IdempotencyRecordModel).where(IdempotencyRecordModel.idempotency_key=="typed-demand"))
        audit=s.scalar(select(AuditLogModel).where(AuditLogModel.action=="ingredient_demand_generated"))
        assert record is not None
        assert record.resource_type=="ingredient_demand_run" and record.resource_id==payload["ingredient_demand_run_id"]
        assert record.response_status==200 and record.response_body_json is None
        assert audit is not None
        assert audit.resource_type=="ingredient_demand_run" and audit.resource_id==payload["ingredient_demand_run_id"]
        assert audit.source=="planning_service" and json.loads(audit.after_json)=={
            "forecast_run_id":"typed-demand","prediction_count":1,
        }
    document=client.get("/openapi.json").json();operation=document["paths"]["/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/ingredient-demand"]
    expected={"$ref":"#/components/schemas/IngredientDemandRunResponse"}
    assert operation["post"]["responses"]["200"]["content"]["application/json"]["schema"]==expected
    assert operation["get"]["responses"]["200"]["content"]["application/json"]["schema"]==expected
    assert len(document["paths"])==58
    assert sum(method in {"get","post","put","patch","delete","head","options","trace"} for item in document["paths"].values() for method in item)==70


def test_planning_facade_delegates_demand_lifecycle(client, monkeypatch):
    service=client.app.state.decision_planning_service
    generated={"status":"generated"};read={"status":"read"}
    monkeypatch.setattr(service.ingredient_demand,"generate",lambda *args:generated)
    monkeypatch.setattr(service.ingredient_demand,"read",lambda *args:read)
    assert service.generate_demand("STORE_001","forecast","key",True)==generated
    assert service.get_demand("STORE_001","forecast")==read


def test_planning_facade_delegates_operational_procurement_lifecycle(client, monkeypatch):
    service=client.app.state.decision_planning_service
    generated={"status":"generated"};read={"status":"read"}
    monkeypatch.setattr(service.operational_procurement,"generate",lambda *args:generated)
    monkeypatch.setattr(service.operational_procurement,"read",lambda *args:read)
    body=ProcurementPlansRequest(strategies=["balanced"])
    assert service.generate_plans("STORE_001","forecast",body,"key")==generated
    assert service.get_plans("STORE_001","forecast","run")==read


def test_planning_facade_delegates_legacy_plan_compatibility(client, monkeypatch):
    service=client.app.state.decision_planning_service
    created={"status":"created"};metadata={"status":"metadata"};result={"status":"result"}
    monkeypatch.setattr(service.legacy_plan_compatibility,"create",lambda *args:created)
    monkeypatch.setattr(service.legacy_plan_compatibility,"read_metadata",lambda *args:metadata)
    monkeypatch.setattr(service.legacy_plan_compatibility,"read_result",lambda *args:result)
    assert service.create_legacy_plan("STORE_001",object(),"key","request")==created
    assert service.get_legacy_plan_metadata("STORE_001","run")==metadata
    assert service.get_legacy_plan_result("STORE_001","run")==result


def test_procurement_plan_typed_response_contract_and_openapi(client):
    sf=client.app.state.session_factory
    with sf() as s:_procurement_input(s,"typed-procurement")
    path="/api/v1/stores/STORE_001/forecast-runs/typed-procurement/procurement-plans"
    body={"strategies":["lean","balanced","protected"],"use_open_purchase_orders":False,"use_latest_inventory":True,"budget_override":1000}
    created=client.post(path,json=body,headers={"Idempotency-Key":"typed-procurement"})
    assert created.status_code==200,created.text
    payload=created.json()
    assert ProcurementPlanRunResponse.model_validate(payload).model_dump(mode="json")==payload
    assert payload["status"]=="completed" and payload["recommended_strategy"]=="balanced"
    assert [plan["strategy"] for plan in payload["plans"]]==["balanced","lean","protected"]
    balanced=payload["plans"][0];line=balanced["lines"][0];projection=balanced["daily_projections"][0]
    assert balanced["is_feasible"] and balanced["is_recommended"]
    assert {"ingredient_id","supplier_id","supplier_term_id","order_date","expected_arrival_date","raw_required_quantity","order_quantity","rounding_excess","unit","pack_count","unit_cost","line_cost","moq","pack_size","lead_time_days","reason_codes","warnings"} == set(line)
    assert all(isinstance(line[key],float) for key in ("raw_required_quantity","order_quantity","rounding_excess","moq","pack_size"))
    assert line["supplier_id"]=="supplier-typed-procurement" and line["order_date"]=="2026-08-04"
    assert isinstance(projection["demand_quantity"],str) and projection["daily"][0]["date"]=="2026-08-04"
    latest=client.get(path);specific=client.get(path,params={"procurement_plan_run_id":payload["procurement_plan_run_id"]})
    assert latest.status_code==specific.status_code==200 and latest.json()==specific.json()==payload
    replay=client.post(path,json=body,headers={"Idempotency-Key":"typed-procurement"})
    assert replay.status_code==200 and replay.json()==payload
    conflict=client.post(path,json={**body,"budget_override":999},headers={"Idempotency-Key":"typed-procurement"})
    assert conflict.status_code==409 and conflict.json()["code"]=="DUPLICATE_REQUEST"
    assert conflict.json()["details"]["reason"]=="IDEMPOTENCY_KEY_REUSED"
    with sf() as s:
        record=s.scalar(select(IdempotencyRecordModel).where(IdempotencyRecordModel.idempotency_key=="typed-procurement"))
        audit=s.scalar(select(AuditLogModel).where(AuditLogModel.action=="procurement_plans_generated"))
        assert record is not None
        assert record.resource_type=="procurement_plan_run" and record.resource_id==payload["procurement_plan_run_id"]
        assert record.response_status==200 and record.response_body_json is None
        assert audit is not None
        assert audit.resource_type=="procurement_plan_run" and audit.resource_id==payload["procurement_plan_run_id"]
        assert audit.source=="planning_service" and json.loads(audit.after_json)=={
            "forecast_run_id":"typed-procurement","recommended_strategy":"balanced",
        }
    document=client.get("/openapi.json").json();operation=document["paths"]["/api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans"]
    expected={"$ref":"#/components/schemas/ProcurementPlanRunResponse"}
    assert operation["post"]["responses"]["200"]["content"]["application/json"]["schema"]==expected
    assert operation["get"]["responses"]["200"]["content"]["application/json"]["schema"]==expected
    assert len(document["paths"])==58
    assert sum(method in {"get","post","put","patch","delete","head","options","trace"} for item in document["paths"].values() for method in item)==70


def test_procurement_plan_typed_response_supports_no_recommendation_and_blocked_run(client):
    sf=client.app.state.session_factory
    with sf() as s:
        _procurement_input(s,"no-feasible-procurement")
        _procurement_input(s,"blocked-procurement",target_date=date(2026,8,3))
    no_feasible_path="/api/v1/stores/STORE_001/forecast-runs/no-feasible-procurement/procurement-plans"
    no_feasible=client.post(no_feasible_path,json={"strategies":["lean","balanced","protected"],"use_open_purchase_orders":False,"use_latest_inventory":True,"budget_override":0})
    assert no_feasible.status_code==200,no_feasible.text
    assert no_feasible.json()["status"]=="completed" and no_feasible.json()["recommended_strategy"] is None
    assert len(no_feasible.json()["plans"])==3 and not any(plan["is_feasible"] for plan in no_feasible.json()["plans"])
    assert ProcurementPlanRunResponse.model_validate(no_feasible.json()).recommended_strategy is None
    blocked_path="/api/v1/stores/STORE_001/forecast-runs/blocked-procurement/procurement-plans"
    blocked_post=client.post(blocked_path,json={"strategies":["balanced"],"use_open_purchase_orders":False,"use_latest_inventory":True})
    assert blocked_post.status_code==422 and blocked_post.json()["code"]=="FORECAST_HORIZON_MISMATCH"
    blocked_get=client.get(blocked_path)
    assert blocked_get.status_code==200 and blocked_get.json()["status"]=="blocked"
    assert blocked_get.json()["failure_code"]=="FORECAST_HORIZON_MISMATCH" and blocked_get.json()["plans"]==[]
    assert ProcurementPlanRunResponse.model_validate(blocked_get.json()).completed_at is not None


def test_procurement_running_commit_and_failure_replay_are_persisted(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:_procurement_input(s,"committed-procurement")
    path="/api/v1/stores/STORE_001/forecast-runs/committed-procurement/procurement-plans"
    body={"strategies":["balanced"],"use_open_purchase_orders":False,"use_latest_inventory":True}

    def fail_after_running_commit(_self,*_args,**_kwargs):
        with sf() as inspection:
            run=inspection.scalar(select(ProcurementPlanRunModel).where(
                ProcurementPlanRunModel.forecast_run_id=="committed-procurement",
            ))
            record=inspection.scalar(select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.idempotency_key=="committed-procurement-key",
            ))
            assert run is not None and run.status=="running"
            assert record is not None
            assert record.resource_type=="procurement_plan_run" and record.resource_id==run.procurement_plan_run_id
            assert record.response_status is None
        replay=client.app.state.decision_planning_service.generate_plans(
            "STORE_001", "committed-procurement", ProcurementPlansRequest(**body),
            "committed-procurement-key",
        )
        assert replay["procurement_plan_run_id"]==run.procurement_plan_run_id
        assert replay["status"]=="running" and replay["plans"]==[]
        raise PlanningError("CHARACTERIZED_BUILD_FAILURE","planner failed after running commit")

    monkeypatch.setattr("app.services.decision.operational_procurement_lifecycle.ProcurementPlanningService.build",fail_after_running_commit)
    failed=client.post(path,json=body,headers={"Idempotency-Key":"committed-procurement-key"})
    assert failed.status_code==422 and failed.json()["code"]=="CHARACTERIZED_BUILD_FAILURE"
    with sf() as s:
        run=s.scalar(select(ProcurementPlanRunModel).where(
            ProcurementPlanRunModel.forecast_run_id=="committed-procurement",
        ))
        record=s.scalar(select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.idempotency_key=="committed-procurement-key",
        ))
        assert run is not None
        assert run.status=="blocked" and run.failure_code=="CHARACTERIZED_BUILD_FAILURE"
        assert run.completed_at is not None
        assert record is not None and record.resource_id==run.procurement_plan_run_id
        assert record.response_status is None
    persisted=client.get(path)
    assert persisted.status_code==200
    assert persisted.json()["status"]=="blocked" and persisted.json()["plans"]==[]
    replay=client.post(path,json=body,headers={"Idempotency-Key":"committed-procurement-key"})
    assert replay.status_code==200 and replay.json()==persisted.json()


def test_procurement_unexpected_failure_is_persisted_as_failed(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:_procurement_input(s,"unexpected-procurement")
    path="/api/v1/stores/STORE_001/forecast-runs/unexpected-procurement/procurement-plans"
    body={"strategies":["balanced"],"use_open_purchase_orders":False,"use_latest_inventory":True}

    def fail_unexpectedly(_self,*_args,**_kwargs):
        raise RuntimeError("characterized unexpected planner failure")

    monkeypatch.setattr("app.services.decision.operational_procurement_lifecycle.ProcurementPlanningService.build",fail_unexpectedly)
    failed=client.post(path,json=body,headers={"Idempotency-Key":"unexpected-procurement-key"})
    assert failed.status_code==500 and failed.json()["code"]=="PROCUREMENT_PLAN_INFEASIBLE"
    with sf() as s:
        run=s.scalar(select(ProcurementPlanRunModel).where(
            ProcurementPlanRunModel.forecast_run_id=="unexpected-procurement",
        ))
        assert run is not None
        audit=s.scalar(select(AuditLogModel).where(
            AuditLogModel.resource_type=="procurement_plan_run",
            AuditLogModel.resource_id==run.procurement_plan_run_id,
        ))
        assert run.status=="failed" and run.failure_code=="PROCUREMENT_PLAN_INFEASIBLE"
        assert run.failure_message=="characterized unexpected planner failure" and run.completed_at is not None
        assert audit is None
    persisted=client.get(path)
    assert persisted.status_code==200 and persisted.json()["status"]=="failed" and persisted.json()["plans"]==[]
    replay=client.post(path,json=body,headers={"Idempotency-Key":"unexpected-procurement-key"})
    assert replay.status_code==200 and replay.json()==persisted.json()


def test_planning_auth_incomplete_forecast_scope_and_ownership(client):
    sf=client.app.state.session_factory
    with sf() as s:
        product=_forecast(s,"scope-forecast",["not-in-bom"])
        ingredient=IngredientModel(ingredient_id="scope-i",store_id="STORE_001",ingredient="I",normalized_name="scope-i",base_unit="kg",active=True,source="test");s.add(ingredient)
        recipe=RecipeVersionModel(recipe_version_id="scope-r",store_id="STORE_001",product_id=product.product_id,version=1,effective_from=date(2026,1,1),content_hash="scope",source="test",yield_quantity=1,process_loss_rate=0);s.add(recipe);s.flush()
        s.add(RecipeLineModel(recipe_line_id="scope-l",recipe_version_id=recipe.recipe_version_id,ingredient_id=ingredient.ingredient_id,quantity=1,unit="kg"))
        _forecast(s,"running-forecast",status="running");s.commit()
    client.app.state.settings.shelfcash_api_key="secret"
    assert client.post("/api/v1/stores/STORE_001/forecast-runs/scope-forecast/ingredient-demand").status_code==401
    headers={"X-ShelfCash-Key":"secret"}
    scoped=client.post("/api/v1/stores/STORE_001/forecast-runs/scope-forecast/ingredient-demand",headers=headers)
    assert scoped.status_code==200 and scoped.json()["predictions"]==[] and "INGREDIENT_SCOPE_NO_MATCH" in scoped.json()["warnings"]
    assert IngredientDemandRunResponse.model_validate(scoped.json()).predictions==[]
    incomplete=client.post("/api/v1/stores/STORE_001/forecast-runs/running-forecast/ingredient-demand",headers=headers)
    assert incomplete.status_code==409 and incomplete.json()["code"]=="FORECAST_RUN_NOT_COMPLETED"
    wrong=client.get("/api/v1/stores/STORE_TEST_001/forecast-runs/scope-forecast/ingredient-demand",headers=headers)
    assert wrong.status_code==404 and wrong.json()["code"]=="FORECAST_RUN_NOT_FOUND"


def test_legacy_plan_preserves_committed_failure_and_idempotency_replay_contract(client, monkeypatch):
    sf=client.app.state.session_factory
    with sf() as s:
        _forecast(s,"legacy-plan-blocked")

    service=client.app.state.decision_planning_service

    def block_nested_demand(store_id, forecast_run_id, key=None, refresh=False):
        with sf() as inspection:
            run=inspection.scalar(select(PlanRunModel).where(
                PlanRunModel.forecast_run_id==forecast_run_id,
            ))
            assert run is not None and run.status=="running"
        raise PlanningError("RECIPE_NOT_FOUND","Recipe is unavailable for this forecast.")

    monkeypatch.setattr(service.legacy_plan_compatibility.ingredient_demand_lifecycle,"generate",block_nested_demand)
    path="/api/v1/stores/STORE_001/plan-runs"
    body={
        "forecast_run_id":"legacy-plan-blocked",
        "strategy":"balanced",
        "budget_limit":1000,
        "as_of_date":"2026-08-03",
        "include_open_purchase_orders":True,
    }
    headers={"Idempotency-Key":"legacy-plan-blocked-key"}
    failed=client.post(path,json=body,headers=headers)
    assert failed.status_code==422 and failed.json()["code"]=="RECIPE_NOT_FOUND"

    with sf() as s:
        run=s.scalar(select(PlanRunModel).where(
            PlanRunModel.forecast_run_id=="legacy-plan-blocked",
        ))
        record=s.scalar(select(IdempotencyRecordModel).where(
            IdempotencyRecordModel.idempotency_key=="legacy-plan-blocked-key",
        ))
        assert run is not None
        assert run.status=="blocked" and run.failure_code=="RECIPE_NOT_FOUND"
        assert run.completed_at is not None and run.procurement_plan_run_id is None
        assert record is not None
        assert record.resource_type=="plan_run" and record.resource_id==run.plan_run_id
        assert record.response_status==200 and record.response_body_json is None

    metadata=client.get(f"{path}/{run.plan_run_id}")
    assert metadata.status_code==200 and metadata.json()["status"]=="blocked"
    result=client.get(f"{path}/{run.plan_run_id}/result")
    assert result.status_code==409 and result.json()["code"]=="RECIPE_NOT_FOUND"
    replay=client.post(path,json=body,headers=headers)
    assert replay.status_code==200
    assert replay.json()["plan_run_id"]==run.plan_run_id
    assert replay.json()["status"]=="blocked"


def test_legacy_plan_persists_compatibility_projection_and_audit(client):
    sf=client.app.state.session_factory
    with sf() as s:
        _procurement_input(s,"legacy-plan-completed")

    path="/api/v1/stores/STORE_001/plan-runs"
    created=client.post(path,json={
        "forecast_run_id":"legacy-plan-completed",
        "strategy":"balanced",
        "budget_limit":1000,
        "as_of_date":"2026-08-03",
        "include_open_purchase_orders":True,
    },headers={"Idempotency-Key":"legacy-plan-completed-key"})
    assert created.status_code==200,created.text
    metadata=created.json()

    with sf() as s:
        recommendations=list(s.scalars(select(RecommendationModel).where(
            RecommendationModel.plan_run_id==metadata["plan_run_id"],
        )))
        audit=s.scalar(select(AuditLogModel).where(
            AuditLogModel.action=="legacy_plan_generated",
            AuditLogModel.resource_id==metadata["plan_run_id"],
        ))
        assert recommendations
        assert audit is not None
        assert audit.resource_type=="plan_run" and audit.source=="planning_service"
        assert json.loads(audit.after_json)=={
            "forecast_run_id":"legacy-plan-completed",
            "procurement_plan_run_id":metadata["procurement_plan_run_id"],
            "strategy":"balanced",
        }
        recommendation_id=recommendations[0].recommendation_id
    purchase_order=client.post("/api/v1/stores/STORE_001/purchase-orders",json={
        "plan_run_id":metadata["plan_run_id"],"lines":[{"recommendation_id":recommendation_id}],
    },headers={"Idempotency-Key":"legacy-plan-completed-po"})
    assert purchase_order.status_code==201,purchase_order.text
    assert purchase_order.json()["orders"][0]["plan_run_id"]==metadata["plan_run_id"]


def test_legacy_plan_finalization_failure_rolls_back_projection_and_marks_failed(client, monkeypatch):
    from app.services.audit_service import AuditService

    sf=client.app.state.session_factory
    with sf() as s:
        _procurement_input(s,"legacy-finalization-failure")
    original_record=AuditService.record

    def fail_only_legacy_audit(self,*args,**kwargs):
        if kwargs.get("action")=="legacy_plan_generated":
            raise RuntimeError("characterized legacy finalization failure")
        return original_record(self,*args,**kwargs)

    monkeypatch.setattr("app.services.decision.legacy_plan_compatibility.AuditService.record",fail_only_legacy_audit)
    path="/api/v1/stores/STORE_001/plan-runs"
    failed=client.post(path,json={
        "forecast_run_id":"legacy-finalization-failure",
        "strategy":"balanced",
        "budget_limit":1000,
        "as_of_date":"2026-08-03",
        "include_open_purchase_orders":True,
    })
    assert failed.status_code==500 and failed.json()["code"]=="PROCUREMENT_PLAN_INFEASIBLE"
    with sf() as s:
        run=s.scalar(select(PlanRunModel).where(PlanRunModel.forecast_run_id=="legacy-finalization-failure"))
        recommendations=list(s.scalars(select(RecommendationModel).where(RecommendationModel.plan_run_id==run.plan_run_id)))
        audit=s.scalar(select(AuditLogModel).where(
            AuditLogModel.action=="legacy_plan_generated",AuditLogModel.resource_id==run.plan_run_id,
        ))
        assert run.status=="failed" and run.failure_code=="PROCUREMENT_PLAN_INFEASIBLE"
        assert run.procurement_plan_run_id is None
        assert recommendations==[] and audit is None
