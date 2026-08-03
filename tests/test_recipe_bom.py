from datetime import date,datetime,timezone
from decimal import Decimal

import pytest

from app.core.exceptions import PlanningError
from app.core.units import normalize_unit
from app.models.business import IngredientModel,ProductModel,RecipeLineModel,RecipeVersionModel
from app.models.operations import ForecastPredictionModel
from app.services.recipe_bom_service import RecipeBomService


def _prediction(pid,day,p25="10",p50="20",p75="30"):
    return ForecastPredictionModel(prediction_id=f"pred-{pid}-{day}",forecast_run_id="run",store_id="STORE_001",product_id=pid,product_name=pid,target_date=day,horizon=1,p25=Decimal(p25),p50=Decimal(p50),p75=Decimal(p75),interval_lower=0,interval_upper=0,baseline_p50=0,calibration_source="test",warnings_json='["INSUFFICIENT_SEASONAL_HISTORY"]')


def test_bom_aggregates_products_yield_loss_and_g_to_kg(session_factory):
    with session_factory() as s:
        ingredient=IngredientModel(ingredient_id="flour",store_id="STORE_001",ingredient="Flour",normalized_name="flour",base_unit="kg",active=True,source="test");s.add(ingredient)
        for pid,qty in (("p1","500"),("p2","250")):
            s.add(ProductModel(product_id=pid,store_id="STORE_001",product=pid,normalized_name=pid,active=True,source="test"))
            rv=RecipeVersionModel(recipe_version_id=f"rv-{pid}",store_id="STORE_001",product_id=pid,version=1,effective_from=date(2026,1,1),content_hash=pid,source="test",yield_quantity=Decimal("2"),process_loss_rate=Decimal("0.05"));s.add(rv);s.flush()
            s.add(RecipeLineModel(recipe_line_id=f"line-{pid}",recipe_version_id=rv.recipe_version_id,ingredient_id="flour",quantity=Decimal(qty),unit="g"))
        s.flush();rows=RecipeBomService(s).expand("STORE_001",[_prediction("p1",date(2026,8,4)),_prediction("p2",date(2026,8,4))])
    assert len(rows)==1 and rows[0]["source_product_count"]==2
    assert rows[0]["p50"]==Decimal("7.875")
    assert rows[0]["p25"]<=rows[0]["p50"]<=rows[0]["p75"]
    assert "INSUFFICIENT_SEASONAL_HISTORY" in rows[0]["warnings"]


def test_bom_uses_recipe_effective_on_each_target_date_and_missing_blocks(session_factory):
    with session_factory() as s:
        s.add(IngredientModel(ingredient_id="i",store_id="STORE_001",ingredient="I",normalized_name="i",base_unit="ml",active=True,source="test"))
        s.add(ProductModel(product_id="p",store_id="STORE_001",product="P",normalized_name="p",active=True,source="test"))
        for rid,start,end,qty,version in (("old",date(2026,1,1),date(2026,8,4),"0.1",1),("new",date(2026,8,5),None,"0.2",2)):
            s.add(RecipeVersionModel(recipe_version_id=rid,store_id="STORE_001",product_id="p",version=version,effective_from=start,effective_to=end,content_hash=rid,source="test",yield_quantity=1,process_loss_rate=0));s.flush()
            s.add(RecipeLineModel(recipe_line_id=f"l-{rid}",recipe_version_id=rid,ingredient_id="i",quantity=Decimal(qty),unit=normalize_unit("liter")))
        s.flush();rows=RecipeBomService(s).expand("STORE_001",[_prediction("p",date(2026,8,4)),_prediction("p",date(2026,8,5))])
        assert [x["p50"] for x in rows]==[Decimal("2000"),Decimal("4000")]
        with pytest.raises(PlanningError) as exc:RecipeBomService(s).expand("STORE_001",[_prediction("missing",date(2026,8,4))])
        assert exc.value.code=="RECIPE_NOT_FOUND"
