import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class ForecastBacktestResidualRequest(StrictModel):
    """Explicit, chronology-safe residual bootstrap request.

    ``model_version`` is the forecast model family used to select these
    residuals at stochastic decision time.  Each historical origin receives
    its own isolated fitted artifact; this operation never overwrites the
    active production artifact.
    """

    store_id: str = Field(min_length=1, max_length=128)
    origin_date_from: date
    origin_date_to: date
    origin_frequency: Literal["daily", "weekly"] = "weekly"
    forecast_horizon: int = Field(ge=1)
    history_days: int | None = Field(default=None, gt=0)
    model_version: str | None = None

    @field_validator("store_id", "model_version")
    @classmethod
    def safe_path_component(cls, value):
        if value is not None and not SAFE_VERSION.fullmatch(value):
            raise ValueError("must contain only letters, numbers, dot, underscore, or hyphen")
        return value

    @model_validator(mode="after")
    def valid_origin_range(self):
        if self.origin_date_from > self.origin_date_to:
            raise ValueError("origin_date_from must be on or before origin_date_to")
        return self


class ForecastResidualCoverage(StrictModel):
    product_id: str
    horizon: int
    residual_count: int
    ready: bool
    reason: str | None = None


class ForecastBacktestResidualResponse(StrictModel):
    store_id: str
    model_version: str
    origins_requested: int
    origins_completed: int
    origins_skipped: int
    residuals_created: int
    residuals_unchanged: int
    residuals_missing_actual: int
    products_evaluated: int
    products_stochastic_ready: int
    products_not_ready: int
    coverage: list[ForecastResidualCoverage]
    stochastic_ready: bool
    warnings: list[str]


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
