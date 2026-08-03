from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.business import IngredientModel,SupplierIngredientTermModel,SupplierModel
from app.services.procurement_planning_service import ProcurementPlanningService


def test_planner_moq_pack_supplier_budget_strategies_and_resimulation(session_factory):
    with session_factory() as s:
        s.add(IngredientModel(ingredient_id="i-plan",store_id="STORE_001",ingredient="Milk",normalized_name="milk-plan",base_unit="kg",active=True,source="test"))
        for supplier_id,cost in (("slow-expensive",20),("fast-cheap",10)):
            s.add(SupplierModel(supplier_id=supplier_id,store_id="STORE_001",supplier=supplier_id,normalized_name=supplier_id,active=True,source="test"));s.flush()
            s.add(SupplierIngredientTermModel(constraint_id=f"term-{supplier_id}",store_id="STORE_001",supplier_id=supplier_id,ingredient_id="i-plan",unit_cost=cost,moq=Decimal("10"),pack_size=Decimal("5"),lead_time_days=0 if supplier_id=="fast-cheap" else 3,safety_stock=Decimal("2"),capacity=None,unit="kg",version=1,active=True,source="test"))
        s.flush()
        forecast=SimpleNamespace(cutoff_date=date(2026,8,3))
        demand=[SimpleNamespace(ingredient_id="i-plan",target_date=date(2026,8,4),unit="kg",p25=Decimal("3"),p50=Decimal("7"),p75=Decimal("13"))]
        plans,recommended=ProcurementPlanningService(s).build("STORE_001",forecast,demand,["lean","balanced","protected"],False,100)
    by={x["strategy"]:x for x in plans}
    assert by["lean"]["lines"][0]["order_quantity"]==10
    assert by["balanced"]["lines"][0]["supplier_id"]=="fast-cheap"
    assert by["protected"]["lines"][0]["order_quantity"]==15
    assert by["protected"]["constraint_violations"][0]["code"]=="BUDGET_EXCEEDED"
    assert recommended=="balanced" and by["balanced"]["projected_shortage_quantity"]==0
