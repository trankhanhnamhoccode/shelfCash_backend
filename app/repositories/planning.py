from sqlalchemy import select

from app.models.planning import (IngredientDemandPredictionModel, IngredientDemandRunModel,
    ProcurementPlanLineModel, ProcurementPlanModel, ProcurementPlanRunModel)


class PlanningRepository:
    def __init__(self, session): self.session = session
    def demand_run_for_forecast(self, forecast_run_id):
        return self.session.scalar(select(IngredientDemandRunModel).where(IngredientDemandRunModel.forecast_run_id==forecast_run_id))
    def demand_predictions(self, demand_run_id):
        return list(self.session.scalars(select(IngredientDemandPredictionModel).where(
            IngredientDemandPredictionModel.ingredient_demand_run_id==demand_run_id).order_by(
            IngredientDemandPredictionModel.target_date,IngredientDemandPredictionModel.ingredient_id)))
    def plan_run(self, run_id): return self.session.get(ProcurementPlanRunModel,run_id)
    def latest_plan_run(self, forecast_run_id):
        return self.session.scalar(select(ProcurementPlanRunModel).where(
            ProcurementPlanRunModel.forecast_run_id==forecast_run_id).order_by(ProcurementPlanRunModel.created_at.desc()))
    def plans(self, run_id):
        return list(self.session.scalars(select(ProcurementPlanModel).where(
            ProcurementPlanModel.procurement_plan_run_id==run_id).order_by(ProcurementPlanModel.strategy)))
    def lines(self, plan_id):
        return list(self.session.scalars(select(ProcurementPlanLineModel).where(
            ProcurementPlanLineModel.procurement_plan_id==plan_id).order_by(ProcurementPlanLineModel.ingredient_id)))
