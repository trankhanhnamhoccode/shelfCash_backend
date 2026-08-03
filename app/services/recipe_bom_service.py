import json
from collections import defaultdict
from decimal import Decimal

from app.core.exceptions import PlanningError, ValidationError
from app.core.units import convert_quantity
from app.models.business import IngredientModel
from app.repositories.recipes import RecipeRepository


D = Decimal


class RecipeBomService:
    def __init__(self, session): self.session=session; self.recipes=RecipeRepository(session)

    def expand(self, store_id, predictions, ingredient_scope=None):
        aggregated=defaultdict(lambda:{"p25":D(0),"p50":D(0),"p75":D(0),"products":set(),"contributions":[],"warnings":set()})
        missing=[];not_effective=[]
        for prediction in predictions:
            recipe=self.recipes.get_active(store_id,prediction.product_id,prediction.target_date)
            if recipe is None:
                target={"product_id":prediction.product_id,"target_date":prediction.target_date.isoformat()}
                (not_effective if self.recipes.get_versions(store_id,prediction.product_id) else missing).append(target);continue
            if D(recipe.yield_quantity)<=0: raise PlanningError("RECIPE_YIELD_INVALID","Recipe yield không hợp lệ.",{"recipe_version_id":recipe.recipe_version_id})
            lines=self.recipes.lines(recipe.recipe_version_id)
            if not lines: raise PlanningError("RECIPE_LINE_INVALID","Recipe không có line.",{"recipe_version_id":recipe.recipe_version_id})
            loss=D(1)+D(recipe.process_loss_rate)
            forecast_warnings=json.loads(prediction.warnings_json or "[]")
            for line in lines:
                ingredient=self.session.get(IngredientModel,line.ingredient_id)
                if ingredient is None or ingredient.store_id!=store_id: raise PlanningError("RECIPE_LINE_INVALID","Ingredient trong recipe không hợp lệ.",{"recipe_line_id":line.recipe_line_id})
                try: qty=convert_quantity(D(line.quantity),line.unit,ingredient.base_unit)
                except ValidationError as exc: raise PlanningError("INGREDIENT_UNIT_CONVERSION_FAILED","Không thể đổi đơn vị ingredient.",exc.details) from exc
                factor=qty/D(recipe.yield_quantity)*loss
                key=(ingredient.ingredient_id,prediction.target_date,ingredient.base_unit,prediction.horizon)
                item=aggregated[key]
                values={q:D(getattr(prediction,q))*factor for q in ("p25","p50","p75")}
                for q,v in values.items(): item[q]+=v
                item["ingredient_name"]=ingredient.ingredient;item["products"].add(prediction.product_id)
                item["warnings"].update(forecast_warnings)
                item["contributions"].append({"product_id":prediction.product_id,"product_name":prediction.product_name,
                    "product_p25":str(prediction.p25),"product_p50":str(prediction.p50),"product_p75":str(prediction.p75),
                    "recipe_quantity":str(line.quantity),"recipe_unit":line.unit,"yield_quantity":str(recipe.yield_quantity),
                    "process_loss_rate":str(recipe.process_loss_rate),"ingredient_p25":str(values["p25"]),
                    "ingredient_p50":str(values["p50"]),"ingredient_p75":str(values["p75"]),
                    "recipe_version_id":recipe.recipe_version_id})
        if missing: raise PlanningError("RECIPE_NOT_FOUND","Thiếu recipe có hiệu lực cho product forecast.",{"missing":missing})
        if not_effective: raise PlanningError("RECIPE_NOT_EFFECTIVE","Recipe không có hiệu lực tại target date.",{"not_effective":not_effective})
        scope=set(ingredient_scope or [])
        result=[]
        for (ingredient_id,target_date,unit,horizon),item in sorted(aggregated.items(),key=lambda x:(x[0][1],x[0][0])):
            if scope and ingredient_id not in scope: continue
            if not item["p25"]<=item["p50"]<=item["p75"]: raise PlanningError("INGREDIENT_DEMAND_INCOMPLETE","Ingredient quantiles không hợp lệ.")
            result.append({"ingredient_id":ingredient_id,"ingredient_name":item["ingredient_name"],"target_date":target_date,
                "horizon":horizon,"unit":unit,"p25":item["p25"],"p50":item["p50"],"p75":item["p75"],
                "source_product_count":len(item["products"]),"contributions":item["contributions"],"warnings":sorted(item["warnings"])})
        return result
