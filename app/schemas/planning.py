from decimal import Decimal
from typing import Any,Literal
from datetime import date,datetime
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class ProcurementPlansRequest(Strict):
    strategies:list[Literal["lean","balanced","protected"]]=Field(default_factory=lambda:["lean","balanced","protected"],min_length=1)
    use_open_purchase_orders:bool=True
    use_latest_inventory:bool=True
    budget_override:int|None=Field(default=None,ge=0)

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
