from sqlalchemy import select

from app.core.exceptions import PlanningError
from app.models.operations import ForecastPredictionModel, ForecastRunModel
from app.repositories.stores import StoreRepository


class CompletedForecastReader:
    """Loads the current completed forecast state used by planning lifecycles."""

    def load(self, session, store_id: str, forecast_run_id: str):
        StoreRepository(session).get_required(store_id)
        run = session.get(ForecastRunModel, forecast_run_id)
        if not run or run.store_id != store_id:
            raise PlanningError(
                "FORECAST_RUN_NOT_FOUND",
                "Không tìm thấy forecast run.",
                {"forecast_run_id": forecast_run_id},
                http_status=404,
            )
        if run.status != "completed":
            raise PlanningError(
                "FORECAST_RUN_NOT_COMPLETED",
                "Forecast run chưa completed.",
                {"status": run.status},
                http_status=409,
            )
        predictions = list(session.scalars(select(ForecastPredictionModel).where(
            ForecastPredictionModel.forecast_run_id == forecast_run_id,
        )))
        if not predictions:
            raise PlanningError(
                "FORECAST_PREDICTIONS_MISSING",
                "Forecast completed nhưng thiếu predictions.",
                http_status=500,
            )
        return run, predictions
