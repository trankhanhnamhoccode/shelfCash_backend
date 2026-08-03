from datetime import date,datetime,timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.models.business import IngredientModel,ProductModel,RecipeLineModel,RecipeVersionModel
from app.models.operations import ForecastPredictionModel,ForecastRunModel
from app.models.planning import IngredientDemandRunModel


def _forecast(session,run_id="planning-forecast",scope=None,status="completed"):
    product=ProductModel(product_id=f"product-{run_id}",store_id="STORE_001",product="Product",normalized_name=f"product-{run_id}",active=True,source="test")
    session.add(product);session.flush()
    run=ForecastRunModel(forecast_run_id=run_id,store_id="STORE_001",cutoff_date=date(2026,8,3),horizon_days=1,
        quantiles_json="[0.25,0.5,0.75]",scope_json='{"ingredient_ids":'+str(scope or []).replace("'",'"')+'}',use_latest_calendar=True,status=status,
        engine_status="forecast_core",request_hash=run_id,model_version="test",warnings_json="[]",created_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc) if status=="completed" else None)
    session.add(run);session.flush()
    session.add(ForecastPredictionModel(prediction_id=str(uuid4()),forecast_run_id=run_id,store_id="STORE_001",product_id=product.product_id,product_name="Product",target_date=date(2026,8,4),horizon=1,p25=10,p50=20,p75=30,interval_lower=9,interval_upper=31,baseline_p50=20,calibration_source="test",warnings_json="[]",created_at=datetime.now(timezone.utc)))
    session.commit();return product


def test_missing_recipe_blocks_demand_without_failing_forecast(client):
    sf=client.app.state.session_factory
    with sf() as s:_forecast(s)
    response=client.post("/api/v1/stores/STORE_001/forecast-runs/planning-forecast/ingredient-demand")
    assert response.status_code==422 and response.json()["code"]=="RECIPE_NOT_FOUND"
    with sf() as s:
        assert s.get(ForecastRunModel,"planning-forecast").status=="completed"
        assert s.scalar(select(IngredientDemandRunModel)).status=="blocked"


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
    incomplete=client.post("/api/v1/stores/STORE_001/forecast-runs/running-forecast/ingredient-demand",headers=headers)
    assert incomplete.status_code==409 and incomplete.json()["code"]=="FORECAST_RUN_NOT_COMPLETED"
    wrong=client.get("/api/v1/stores/STORE_TEST_001/forecast-runs/scope-forecast/ingredient-demand",headers=headers)
    assert wrong.status_code==404 and wrong.json()["code"]=="FORECAST_RUN_NOT_FOUND"
