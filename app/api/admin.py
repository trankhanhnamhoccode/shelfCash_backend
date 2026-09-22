from fastapi import APIRouter, Depends, Request

from app.dependencies import get_forecast_service, require_admin_api_key
from app.schemas.forecast import ForecastBacktestResidualRequest, ForecastBacktestResidualResponse


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_api_key)])


@router.post("/forecast-models/backtest-residuals", response_model=ForecastBacktestResidualResponse)
def backtest_residuals(
    body: ForecastBacktestResidualRequest,
    request: Request,
    service=Depends(get_forecast_service),
):
    return service.backtest_residuals(body, getattr(request.state, "request_id", None))
