from decimal import Decimal
from typing import Any,Literal
from datetime import date,datetime
from pydantic import BaseModel, ConfigDict, Field, JsonValue

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class ProcurementPlansRequest(Strict):
    strategies:list[Literal["lean","balanced","protected"]]=Field(default_factory=lambda:["lean","balanced","protected"],min_length=1)
    use_open_purchase_orders:bool=True
    use_latest_inventory:bool=True
    budget_override:int|None=Field(default=None,ge=0)


class IngredientDemandContributionResponse(Strict):
    """Current public BOM evidence emitted by ``CoreBomAdapter``."""

    product_id: str; product_name: str; product_unit: str | None
    recipe_id: str; recipe_version: str; recipe_line_id: str | None
    forecast_p25: float; forecast_p50: float; forecast_p75: float
    recipe_quantity: float; recipe_unit: str
    yield_quantity: float; yield_unit: str
    process_loss_rate: float; waste_allowance_rate: float
    base_contribution_p25: float; base_contribution_p50: float; base_contribution_p75: float
    contribution_p25: float; contribution_p50: float; contribution_p75: float
    contribution_unit: str
    # These established evidence fields intentionally remain strings.  The
    # adapter serializes them this way to preserve the existing API contract.
    product_p25: str; product_p50: str; product_p75: str
    ingredient_p25: str; ingredient_p50: str; ingredient_p75: str
    recipe_version_id: str


class IngredientDemandPredictionResponse(Strict):
    ingredient_id: str; ingredient_name: str; target_date: date; horizon: int; unit: str
    p25: float; p50: float; p75: float
    source_product_count: int
    contributions: list[IngredientDemandContributionResponse]
    warnings: list[str]


class IngredientDemandRunResponse(Strict):
    ingredient_demand_run_id: str; forecast_run_id: str; store_id: str; status: str
    warnings: list[str]
    failure_code: str | None; failure_message: str | None
    created_at: datetime; completed_at: datetime | None
    predictions: list[IngredientDemandPredictionResponse]


class ProcurementConsumedLotResponse(Strict):
    lot_id: str; quantity: str


class ProcurementDailyInventoryResponse(Strict):
    ingredient_id: str; date: date; unit: str
    opening_inventory: str; inbound_quantity: str; demand_quantity: str; fulfilled_quantity: str
    shortage_quantity: str; expired_quantity: str; waste_quantity: str; ending_inventory: str
    consumed_lots: list[ProcurementConsumedLotResponse]


class ProcurementDailyProjectionResponse(Strict):
    ingredient_id: str; unit: str
    opening_inventory: str; inbound_quantity: str; demand_quantity: str; fulfilled_quantity: str
    shortage_quantity: str; expired_quantity: str; waste_quantity: str; ending_inventory: str
    days_of_supply: str | None
    projected_stockout_date: date | None; first_shortage_date: date | None
    at_risk_expiry_quantity: str; fill_rate: str
    daily: list[ProcurementDailyInventoryResponse]


class ProcurementPlanLineResponse(Strict):
    ingredient_id: str; supplier_id: str | None; supplier_term_id: str | None
    order_date: date; expected_arrival_date: date | None
    raw_required_quantity: float; order_quantity: float; rounding_excess: float; unit: str
    pack_count: int | None; unit_cost: int | None; line_cost: int
    moq: float | None; pack_size: float | None; lead_time_days: int | None
    reason_codes: list[str]; warnings: list[str]


class ProcurementPlanResponse(Strict):
    procurement_plan_id: str; strategy: Literal["lean", "balanced", "protected"]
    is_feasible: bool; is_recommended: bool; total_purchase_cost: int
    projected_shortage_quantity: float; projected_waste_quantity: float; fill_rate: float; budget_used: int
    metrics: dict[str, JsonValue]
    warnings: list[str]
    daily_projections: list[ProcurementDailyProjectionResponse]
    lines: list[ProcurementPlanLineResponse]


class ProcurementPlanRunResponse(Strict):
    procurement_plan_run_id: str; forecast_run_id: str; ingredient_demand_run_id: str; store_id: str
    status: str; recommended_strategy: Literal["lean", "balanced", "protected"] | None
    warnings: list[str]; failure_code: str | None; failure_message: str | None
    created_at: datetime; completed_at: datetime | None
    plans: list[ProcurementPlanResponse]


class LegacyPlanMetadataResponse(Strict):
    plan_run_id:str;store_id:str;forecast_run_id:str;procurement_plan_run_id:str|None
    status:str;engine_status:str;strategy:str;planning_strategy:str
    budget_limit:int;as_of_date:date;include_open_purchase_orders:bool
    created_at:datetime;completed_at:datetime|None;result_url:str
    warnings:list[str];failure_code:str|None;failure_message:str|None

class LegacyPlanResultResponse(LegacyPlanMetadataResponse):
    is_feasible:bool;is_recommended:bool;total_purchase_cost:int
    projected_shortage_quantity:float;projected_waste_quantity:float;fill_rate:float
    budget_used:int;budget_remaining:int|None
    constraint_violations:list[dict[str,Any]];plan_lines:list[dict[str,Any]]
    budget_trace:dict[str,Any]
    storage_capacity_trace:dict[str,Any]
    shelf_life_trace:dict[str,Any]
    simulation_summary:list[dict[str,Any]]
