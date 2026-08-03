import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastTrainRequest(StrictModel):
    store_id: str = Field(min_length=1, max_length=128)
    cutoff_date: date
    model_version: str | None = None
    history_days: int | None = Field(default=None, gt=0)

    @field_validator("store_id", "model_version")
    @classmethod
    def safe_path_component(cls, value):
        if value is not None and not SAFE_VERSION.fullmatch(value):
            raise ValueError("must contain only letters, numbers, dot, underscore, or hyphen")
        return value


class ForecastPredictRequest(ForecastTrainRequest):
    forecast_horizon: int = Field(ge=1)


class ForecastPredictionResponse(StrictModel):
    product_id: str; product_name: str; target_date: date; horizon: int
    p25: float; p50: float; p75: float; interval_lower: float; interval_upper: float
    baseline_p50: float; calibration_source: str; warnings: list[str]


class ForecastResponse(StrictModel):
    forecast_run_id: str; store_id: str; forecast_date: date; forecast_horizon: int
    model_version: str; status: str; predictions: list[ForecastPredictionResponse]
    warnings: list[str]; created_at: datetime; completed_at: datetime | None


class ForecastTrainingResponse(StrictModel):
    store_id: str; model_version: str; status: str; trained_at: datetime
    history_start: date; history_end: date; metrics: dict[str, Any]; warnings: list[str]


class LegacyForecastMetadataResponse(StrictModel):
    forecast_run_id: str; store_id: str; status: str; engine_status: str
    cutoff_date: date; horizon_days: int; model_version: str | None
    warnings: list[str]; failure_code: str | None; failure_message: str | None
    created_at: datetime; completed_at: datetime | None; result_url: str


class LegacyForecastResultResponse(LegacyForecastMetadataResponse):
    forecast_date: date | None = None
    forecast_horizon: int | None = None
    predictions: list[ForecastPredictionResponse]
