"""Public API for ShelfCash Forecast Core."""

from shelfcash_forecast.config import ForecastConfig
from shelfcash_forecast.contracts import ForecastPackage, TrainingResult
from shelfcash_forecast.pipeline.inference_pipeline import predict_demand
from shelfcash_forecast.pipeline.training_pipeline import train_forecast_core

__all__ = [
    "ForecastConfig",
    "ForecastPackage",
    "TrainingResult",
    "predict_demand",
    "train_forecast_core",
]
