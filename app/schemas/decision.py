from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionRunRequest(_Strict):
    forecast_run_id: str
    as_of_date: date
    horizon_days: int = Field(ge=1, le=90)
    engine_mode: Literal["legacy", "deterministic", "stochastic"] | None = None
    include_open_purchase_orders: bool = True
    budget_override: int | None = Field(default=None, ge=0)
    scenario_count: int | None = Field(default=None, ge=1, le=1000)
    random_seed: int | None = None


class ExplanationRequest(_Strict):
    language: Literal["vi", "en"] = "vi"
    detail_level: Literal["simple", "manager", "technical"] = "simple"
    question: str | None = Field(default=None, max_length=2000)


class WhatIfRequest(_Strict):
    demand_multiplier: float | None = Field(default=None, gt=0)
    supplier_delay_days: int | None = Field(default=None, ge=0)
    budget_limit: int | None = Field(default=None, ge=0)
    strategy: Literal["lean", "balanced", "protected"] | None = None


DecisionPackage = dict[str, Any]
