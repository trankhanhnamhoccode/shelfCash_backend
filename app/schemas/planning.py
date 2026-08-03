from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class ProcurementPlansRequest(Strict):
    strategies:list[Literal["lean","balanced","protected"]]=Field(default_factory=lambda:["lean","balanced","protected"],min_length=1)
    use_open_purchase_orders:bool=True
    use_latest_inventory:bool=True
    budget_override:int|None=Field(default=None,ge=0)
