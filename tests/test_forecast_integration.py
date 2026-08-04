from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.business import (CalendarFeatureModel,IngredientModel,InventoryLotModel,
    InventoryMovementModel,ProductModel,RecipeLineModel,RecipeVersionModel,SalesDailyModel,
    SupplierIngredientTermModel,SupplierModel)
from app.schemas.forecast import ForecastPredictRequest, ForecastTrainRequest
from shelfcash_forecast import ForecastConfig


def test_db_to_real_core_to_persisted_predictions(client, monkeypatch):
    cutoff=date(2026,8,3); sf=client.app.state.session_factory
    client.app.state.settings.forecast_max_horizon=2
    config=ForecastConfig(horizons=(1,2), minimum_history_observations=28,
        calibration_days=7,test_days=7,minimum_calibration_samples=3,
        walk_forward_minimum_train_days=40,walk_forward_validation_days=7,
        walk_forward_step_days=7,walk_forward_maximum_folds=1,
        lightgbm_params={"learning_rate":0.1,"n_estimators":12,"num_leaves":7,
            "min_child_samples":5,"random_state":42,"n_jobs":1,"verbosity":-1})
    monkeypatch.setattr(client.app.state.forecast_service,"_config",lambda:config)
    with sf() as s:
        product=ProductModel(product_id="real-core-product",store_id="STORE_001",product="Tea",normalized_name="tea",active=True,source="test")
        s.add(product)
        for offset in range(112):
            day=cutoff-timedelta(days=111-offset)
            s.add(SalesDailyModel(sales_record_id=str(uuid4()),store_id="STORE_001",date=day,
                product_id=product.product_id,quantity=Decimal(str(8 + offset % 7)),promotion=False,is_stockout=None,source="test"))
        for offset in range(114):
            day=cutoff-timedelta(days=111)+timedelta(days=offset)
            s.add(CalendarFeatureModel(calendar_feature_id=str(uuid4()),store_id="STORE_001",date=day,
                is_weekend=day.weekday()>=5,is_holiday=False,is_store_closed=False,is_promotion=False,source="test"))
        s.commit()
    trained=client.app.state.forecast_service.train(ForecastTrainRequest(store_id="STORE_001",cutoff_date=cutoff,
        model_version="integration-v1",history_days=112))
    assert trained["status"] == "ready"
    created=client.post("/api/v1/stores/STORE_001/forecast-runs",json={
        "cutoff_date":cutoff.isoformat(),"horizon_days":2,"quantiles":[0.25,0.5,0.75],
        "scope":{"ingredient_ids":[]},"use_latest_calendar":True},headers={"Idempotency-Key":"real-core-run"})
    assert created.status_code == 200, created.text
    metadata=created.json(); assert metadata["status"] == "completed"
    assert metadata["model_version"] == "integration-v1" and metadata["store_id"] == "STORE_001"
    run_id=metadata["forecast_run_id"]
    replay=client.post("/api/v1/stores/STORE_001/forecast-runs",json={
        "cutoff_date":cutoff.isoformat(),"horizon_days":2,"quantiles":[0.25,0.5,0.75],
        "scope":{"ingredient_ids":[]},"use_latest_calendar":True},headers={"Idempotency-Key":"real-core-run"})
    assert replay.status_code == 200 and replay.json()["forecast_run_id"] == run_id
    with sf() as s:
        s.add(IngredientModel(ingredient_id="tea-leaf",store_id="STORE_001",ingredient="Tea leaf",normalized_name="tea-leaf",base_unit="kg",active=True,source="test"))
        s.add(RecipeVersionModel(recipe_version_id="tea-recipe",store_id="STORE_001",product_id="real-core-product",version=1,effective_from=date(2026,1,1),content_hash="tea-recipe",source="test",yield_quantity=Decimal("2"),process_loss_rate=Decimal("0.05")));s.flush()
        s.add(RecipeLineModel(recipe_line_id="tea-line",recipe_version_id="tea-recipe",ingredient_id="tea-leaf",quantity=Decimal("200"),unit="g"))
        s.add(SupplierModel(supplier_id="tea-supplier",store_id="STORE_001",supplier="Tea supplier",normalized_name="tea-supplier",active=True,source="test"));s.flush()
        s.add(SupplierIngredientTermModel(constraint_id="tea-term",store_id="STORE_001",supplier_id="tea-supplier",ingredient_id="tea-leaf",unit_cost=100,moq=Decimal("1"),pack_size=Decimal("0.5"),lead_time_days=0,unit="kg",version=1,active=True,source="test"))
        s.add(InventoryLotModel(lot_id="tea-lot",store_id="STORE_001",ingredient_id="tea-leaf",supplier_id="tea-supplier",received_date=cutoff,expiry_date=date(2026,8,5),initial_quantity=Decimal("1"),unit="kg",unit_cost=100,source="test",version=1));s.flush()
        from datetime import datetime,timezone
        s.add(InventoryMovementModel(movement_id=str(uuid4()),store_id="STORE_001",lot_id="tea-lot",movement_type="opening_balance",quantity_delta=Decimal("1"),unit="kg",occurred_at=datetime(2026,8,3,tzinfo=timezone.utc),source="test"));s.commit()
    demand_created=client.post(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/ingredient-demand",headers={"Idempotency-Key":"demand-real"})
    assert demand_created.status_code==200,demand_created.text
    demand=demand_created.json();assert demand["predictions"] and demand["predictions"][0]["ingredient_id"]=="tea-leaf"
    contribution=demand["predictions"][0]["contributions"][0]
    assert Decimal(contribution["ingredient_p50"])==Decimal(contribution["product_p50"])*Decimal("0.2")/Decimal("2")*Decimal("1.05")
    demand_replay=client.post(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/ingredient-demand",headers={"Idempotency-Key":"demand-real"})
    assert demand_replay.json()["ingredient_demand_run_id"]==demand["ingredient_demand_run_id"]
    plans_created=client.post(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/procurement-plans",headers={"Idempotency-Key":"plans-real"},json={"strategies":["lean","balanced","protected"],"use_open_purchase_orders":True,"use_latest_inventory":True,"budget_override":100000})
    assert plans_created.status_code==200,plans_created.text
    plans=plans_created.json();assert {x["strategy"] for x in plans["plans"]}=={"lean","balanced","protected"}
    balanced=next(x for x in plans["plans"] if x["strategy"]=="balanced")
    assert balanced["lines"][0]["supplier_id"]=="tea-supplier"
    assert Decimal(str(balanced["lines"][0]["order_quantity"]))%Decimal("0.5")==0
    assert plans["recommended_strategy"] in {"lean","balanced","protected"}
    legacy_body={"forecast_run_id":run_id,"strategy":"balanced","budget_limit":100000,
        "as_of_date":cutoff.isoformat(),"include_open_purchase_orders":True}
    legacy=client.post("/api/v1/stores/STORE_001/plan-runs",json=legacy_body,
        headers={"Idempotency-Key":"legacy-plan-real"})
    assert legacy.status_code==200,legacy.text
    legacy_metadata=legacy.json();legacy_id=legacy_metadata["plan_run_id"]
    assert legacy_metadata["status"]=="completed"
    assert legacy_metadata["engine_status"]=="decision_planning"
    assert legacy_metadata["planning_strategy"]=="balanced"
    replay_legacy=client.post("/api/v1/stores/STORE_001/plan-runs",json=legacy_body,
        headers={"Idempotency-Key":"legacy-plan-real"})
    assert replay_legacy.status_code==200 and replay_legacy.json()["plan_run_id"]==legacy_id
    monkeypatch.setattr("app.services.recipe_bom_service.RecipeBomService.expand",
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("GET must not expand BOM")))
    monkeypatch.setattr("app.services.procurement_planning_service.ProcurementPlanningService.build",
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("GET must not run planner")))
    legacy_get=client.get(f"/api/v1/stores/STORE_001/plan-runs/{legacy_id}")
    assert legacy_get.status_code==200 and legacy_get.json()==legacy_metadata
    legacy_result=client.get(f"/api/v1/stores/STORE_001/plan-runs/{legacy_id}/result")
    assert legacy_result.status_code==200,legacy_result.text
    legacy_plan=legacy_result.json()
    assert legacy_plan["strategy"]=="balanced" and legacy_plan["plan_lines"]
    assert legacy_plan["plan_lines"][0]["ingredient_id"]=="tea-leaf"
    assert legacy_plan["plan_lines"][0]["supplier_id"]=="tea-supplier"
    assert Decimal(str(legacy_plan["plan_lines"][0]["order_quantity"]))%Decimal("0.5")==0
    assert legacy_plan["total_purchase_cost"]==balanced["total_purchase_cost"]
    assert legacy_plan["projected_shortage_quantity"]==balanced["projected_shortage_quantity"]
    wrong_store=client.get(f"/api/v1/stores/STORE_TEST_001/plan-runs/{legacy_id}/result")
    assert wrong_store.status_code==404 and wrong_store.json()["code"]=="PLANNING_RUN_NOT_FOUND"
    changed={**legacy_body,"budget_limit":99999}
    conflict=client.post("/api/v1/stores/STORE_001/plan-runs",json=changed,
        headers={"Idempotency-Key":"legacy-plan-real"})
    assert conflict.status_code==409
    monkeypatch.setattr("app.services.forecast_service.predict_demand",
                        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("GET must not infer")))
    fetched=client.get(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}")
    assert fetched.status_code == 200 and fetched.json()["forecast_run_id"] == run_id
    result=client.get(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/result")
    assert result.status_code == 200, result.text
    predictions=result.json()["predictions"]
    assert predictions and {p["product_id"] for p in predictions} == {"real-core-product"}
    assert all(p["p25"] <= p["p50"] <= p["p75"] for p in predictions)
    assert {p["target_date"] for p in predictions} == {"2026-08-04","2026-08-05"}
    alias=client.get(f"/api/v1/forecasts/{run_id}?store_id=STORE_001")
    assert alias.status_code == 200 and alias.json()["predictions"] == predictions
    with sf() as s:
        from sqlalchemy import func, select
        from app.models.operations import ForecastPredictionModel, ForecastRunModel
        from app.models.operations import PlanRunModel
        from app.models.planning import ProcurementPlanRunModel
        assert s.scalar(select(func.count()).select_from(ForecastRunModel)) == 1
        assert s.scalar(select(func.count()).select_from(ForecastPredictionModel)) == len(predictions)
        assert s.scalar(select(func.count()).select_from(PlanRunModel)) == 1
        assert s.scalar(select(func.count()).select_from(ProcurementPlanRunModel)) == 2
    demand_get=client.get(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/ingredient-demand")
    plans_get=client.get(f"/api/v1/stores/STORE_001/forecast-runs/{run_id}/procurement-plans")
    assert demand_get.json()==demand_replay.json()
    assert plans_get.json()["procurement_plan_run_id"]==legacy_metadata["procurement_plan_run_id"]
    latest_balanced=plans_get.json()["plans"][0]
    assert latest_balanced["strategy"]=="balanced"
    assert latest_balanced["lines"]==legacy_plan["plan_lines"] or (
        latest_balanced["lines"][0]["order_quantity"]==legacy_plan["plan_lines"][0]["order_quantity"]
        and latest_balanced["lines"][0]["supplier_id"]==legacy_plan["plan_lines"][0]["supplier_id"])
