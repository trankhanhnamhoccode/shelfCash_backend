from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.operations import ForecastModelVersionModel, ForecastPredictionModel, ForecastRunModel


class ForecastRepository:
    def __init__(self, session: Session): self.session = session

    def active_model(self, store_id: str):
        return self.session.scalar(select(ForecastModelVersionModel).where(
            ForecastModelVersionModel.store_id == store_id,
            ForecastModelVersionModel.is_active.is_(True), ForecastModelVersionModel.status == "ready"))

    def model(self, store_id: str, version: str):
        return self.session.scalar(select(ForecastModelVersionModel).where(
            ForecastModelVersionModel.store_id == store_id, ForecastModelVersionModel.model_version == version))

    def deactivate_all(self, store_id: str):
        self.session.execute(update(ForecastModelVersionModel).where(
            ForecastModelVersionModel.store_id == store_id,
            ForecastModelVersionModel.is_active.is_(True)).values(is_active=False, status="inactive"))

    def run(self, run_id: str): return self.session.get(ForecastRunModel, run_id)

    def predictions(self, run_id: str):
        return list(self.session.scalars(select(ForecastPredictionModel).where(
            ForecastPredictionModel.forecast_run_id == run_id).order_by(
            ForecastPredictionModel.target_date, ForecastPredictionModel.product_id)))
