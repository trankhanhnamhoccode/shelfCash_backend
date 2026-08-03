from fastapi import APIRouter, Depends, Request

from app.dependencies import get_forecast_service, require_api_key
from app.schemas.forecast import (ForecastPredictRequest, ForecastResponse,
                                  ForecastTrainRequest, ForecastTrainingResponse)

router = APIRouter(tags=["forecast"], dependencies=[Depends(require_api_key)])


@router.post("/forecast-models/train", response_model=ForecastTrainingResponse)
def train_forecast(body: ForecastTrainRequest, request: Request, service=Depends(get_forecast_service)):
    return service.train(body, getattr(request.state, "request_id", None))


@router.post("/forecasts", response_model=ForecastResponse, status_code=201, deprecated=True)
def create_forecast(body: ForecastPredictRequest, request: Request, service=Depends(get_forecast_service)):
    return service.predict(body, getattr(request.state, "request_id", None))


@router.get("/forecasts/{forecast_run_id}", response_model=ForecastResponse, deprecated=True)
def get_forecast(forecast_run_id: str, store_id: str | None = None, service=Depends(get_forecast_service)):
    return service.get(forecast_run_id, store_id)
