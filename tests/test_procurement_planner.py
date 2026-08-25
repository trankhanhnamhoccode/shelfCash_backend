from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.business import IngredientModel,InventoryConstraintModel,SupplierIngredientTermModel,SupplierModel
from app.services.procurement_planning_service import ProcurementPlanningService
from app.services.business_constraint_resolver import BusinessConstraintResolver


def test_planner_moq_pack_supplier_budget_strategies_and_resimulation(session_factory):
    with session_factory() as s:
        s.add(IngredientModel(ingredient_id="i-plan",store_id="STORE_001",ingredient="Milk",normalized_name="milk-plan",base_unit="kg",active=True,source="test"))
        for supplier_id,cost in (("slow-expensive",20),("fast-cheap",10)):
            s.add(SupplierModel(supplier_id=supplier_id,store_id="STORE_001",supplier=supplier_id,normalized_name=supplier_id,active=True,source="test"));s.flush()
            s.add(SupplierIngredientTermModel(constraint_id=f"term-{supplier_id}",store_id="STORE_001",supplier_id=supplier_id,ingredient_id="i-plan",unit_cost=cost,moq=Decimal("10"),pack_size=Decimal("5"),lead_time_days=0 if supplier_id=="fast-cheap" else 3,unit="kg",version=1,active=True,source="test"))
        s.add(InventoryConstraintModel(constraint_id="safety-plan",store_id="STORE_001",ingredient_id="i-plan",constraint_type="safety_stock",value=Decimal("2"),unit="kg",effective_date=date(2026,7,1),version=1,active=True,source="test"))
        s.flush()
        forecast=SimpleNamespace(cutoff_date=date(2026,8,3))
        demand=[SimpleNamespace(ingredient_id="i-plan",target_date=date(2026,8,4),unit="kg",p25=Decimal("3"),p50=Decimal("7"),p75=Decimal("13"))]
        plans,recommended=ProcurementPlanningService(s).build("STORE_001",forecast,demand,["lean","balanced","protected"],False,100)
    by={x["strategy"]:x for x in plans}
    assert by["lean"]["lines"][0]["order_quantity"]==10
    assert by["balanced"]["lines"][0]["supplier_id"]=="fast-cheap"
    assert by["protected"]["lines"][0]["order_quantity"]==15
    assert by["protected"]["constraint_violations"][0]["code"]=="BUDGET_EXCEEDED"
    assert by["protected"]["budget_trace"]["source"] == "request_override"
    assert recommended=="balanced" and by["balanced"]["projected_shortage_quantity"]==0
    assert by["balanced"]["shelf_life_trace"] == {}
    # A zero-lead supplier may arrive on the first planning day, but not on
    # the EOD historical cutoff (2026-08-03).
    assert by["balanced"]["lines"][0]["order_date"] == date(2026,8,4)
    assert by["balanced"]["lines"][0]["expected_arrival_date"] == date(2026,8,4)


def _projection(unit, daily):
    return {"unit": unit, "daily": [
        {"date": day, "opening_inventory": opening, "inbound_quantity": inbound, "ending_inventory": ending}
        for day, opening, inbound, ending in daily
    ]}


def _capacity(unit="lít", value="650", constraint_type="storage_capacity"):
    return SimpleNamespace(constraint_id="capacity-1", constraint_type=constraint_type,
        value=Decimal(value), unit=unit)


def test_storage_capacity_missing_has_distinct_trace_and_warning():
    trace, warning, violation = ProcurementPlanningService._evaluate_storage_capacity(None, [])
    assert trace["configured"] is False
    assert trace["evaluation_status"] == "not_configured"
    assert warning == "STORAGE_CAPACITY_NOT_CONFIGURED"
    assert violation is None


def test_storage_capacity_alias_and_mixed_dimensions_warn_without_violation():
    projections = [
        _projection("liter", [("2026-08-04", 100, 0, 90)]),
        _projection("kg", [("2026-08-04", 20, 0, 18)]),
        _projection("unit", [("2026-08-04", 30, 0, 25)]),
    ]
    trace, warning, violation = ProcurementPlanningService._evaluate_storage_capacity(_capacity(), projections)
    assert trace == {
        "configured": True, "constraint_id": "capacity-1", "constraint_type": "storage_capacity",
        "configured_value": 650.0, "configured_unit": "lít", "canonical_value": 650.0,
        "canonical_unit": "liter", "evaluation_status": "dimension_unsupported",
        "reason": "mixed_inventory_dimensions", "inventory_dimensions": ["count", "mass", "volume"],
    }
    assert warning == "STORAGE_CAPACITY_DIMENSION_UNSUPPORTED"
    assert violation is None


def test_storage_capacity_single_dimension_within_and_exceeded():
    within = [
        _projection("liter", [("2026-08-04", 200, 100, 250), ("2026-08-05", 250, 0, 220)]),
        _projection("ml", [("2026-08-04", 100000, 0, 90000), ("2026-08-05", 90000, 0, 80000)]),
    ]
    trace, warning, violation = ProcurementPlanningService._evaluate_storage_capacity(_capacity(), within)
    assert trace["evaluation_status"] == "within_capacity"
    assert trace["peak_date"] == "2026-08-04" and trace["peak_value"] == 400.0
    assert warning is None and violation is None

    exceeded = [_projection("litre", [("2026-08-04", 500, 200, 680), ("2026-08-05", 680, 0, 600)])]
    trace, warning, violation = ProcurementPlanningService._evaluate_storage_capacity(_capacity(), exceeded)
    assert trace["evaluation_status"] == "exceeded"
    assert trace["peak_date"] == "2026-08-04"
    assert trace["peak_value"] == 700.0 and trace["excess_quantity"] == 50.0
    assert warning == "STORAGE_CAPACITY_EXCEEDED"
    assert violation["code"] == "STORAGE_CAPACITY_EXCEEDED"


def test_storage_capacity_resolution_uses_explicit_type_priority(session_factory):
    with session_factory() as session:
        for constraint_type in ("warehouse_capacity", "storage_capacity", "maximum_storage_volume"):
            session.add(InventoryConstraintModel(constraint_id=f"cap-{constraint_type}", store_id="STORE_001",
                ingredient_id=None, constraint_type=constraint_type, value=Decimal("650"), unit="liter",
                effective_date=date(2026, 7, 1), version=1, active=True, source="test"))
        session.flush()
        resolved = BusinessConstraintResolver(session).resolve_storage_capacity("STORE_001", date(2026, 8, 4))
        assert resolved.constraint_type == "maximum_storage_volume"


def test_storage_capacity_does_not_bypass_ingredient_maximum_stock(session_factory):
    with session_factory() as session:
        session.add(IngredientModel(ingredient_id="max-stock-item", store_id="STORE_001", ingredient="Capacity maximum item",
            normalized_name="capacity-maximum-item-unique", base_unit="lít", active=True, source="test"))
        session.add(SupplierModel(supplier_id="max-stock-supplier", store_id="STORE_001", supplier="Supplier",
            normalized_name="max-stock-supplier", active=True, source="test")); session.flush()
        session.add(SupplierIngredientTermModel(constraint_id="max-stock-term", store_id="STORE_001",
            supplier_id="max-stock-supplier", ingredient_id="max-stock-item", unit_cost=1, moq=Decimal("1"),
            pack_size=Decimal("1"), lead_time_days=0, unit="lít", version=1, active=True, source="test"))
        session.add(InventoryConstraintModel(constraint_id="maximum-stock", store_id="STORE_001",
            ingredient_id="max-stock-item", constraint_type="maximum_stock", value=Decimal("4"), unit="lít",
            effective_date=date(2026, 7, 1), version=1, active=True, source="test"))
        session.add(InventoryConstraintModel(constraint_id="store-capacity", store_id="STORE_001",
            ingredient_id=None, constraint_type="storage_capacity", value=Decimal("650"), unit="lít",
            effective_date=date(2026, 7, 1), version=1, active=True, source="test")); session.flush()
        forecast = SimpleNamespace(cutoff_date=date(2026, 8, 3))
        demand = [SimpleNamespace(ingredient_id="max-stock-item", target_date=date(2026, 8, 4), unit="lít",
            p25=Decimal("10"), p50=Decimal("10"), p75=Decimal("10"))]
        plans, _ = ProcurementPlanningService(session).build("STORE_001", forecast, demand, ["balanced"], False, 1000)
        assert plans[0]["constraint_trace"]["max-stock-item"]["maximum_stock"] == "4.000000"
        assert plans[0]["lines"][0]["raw_required_quantity"] == Decimal("4.000000")
        assert plans[0]["storage_capacity_trace"]["evaluation_status"] == "within_capacity"


def _shelf_term(moq="5", pack="5"):
    return SimpleNamespace(unit="kg", moq=Decimal(moq), pack_size=Decimal(pack))


def _shelf_demands(quantities):
    return [{"date": date(2026, 8, 4 + index), "quantity": Decimal(str(quantity))}
        for index, quantity in enumerate(quantities)]


def _empty_baseline(service, demands):
    return service.simulator.simulate("shelf-item", "kg", demands, [], [])


def test_shelf_life_policy_reduces_packs_and_resimulates_without_fake_expiry():
    service = ProcurementPlanningService(SimpleNamespace())
    demands = _shelf_demands([2, 2, 2, 2, 2, 10])
    baseline = _empty_baseline(service, demands)
    captured = []
    original_simulate = service.simulator.simulate
    def recording_simulate(*args, **kwargs):
        inbound = args[4] if len(args) > 4 else kwargs.get("inbound", [])
        captured.extend(inbound)
        return original_simulate(*args, **kwargs)
    service.simulator.simulate = recording_simulate
    order, packs, trace, reasons, warnings = service._apply_shelf_life_policy(
        "shelf-item", "kg", demands, [], [], baseline, _shelf_term(), date(2026, 8, 4),
        Decimal("20"), 4, Decimal("0"), None, 5)
    assert order == 10 and packs == 2
    assert trace["configured_days"] == 5
    assert trace["demand_window_end"] == "2026-08-08"
    assert trace["demand_within_window"] == "10"
    assert trace["maximum_usable_replenishment"] == "10"
    assert trace["decision"] == "reduced" and trace["quantity_at_risk"] == "0"
    assert reasons == [] and warnings == []
    assert captured and all("expiry_date" not in event for event in captured)
    expired = service._simulate_candidate("shelf-item", "kg", _shelf_demands([1]),
        [{"lot_id":"real-expiry","quantity":Decimal("3"),"expiry_date":date(2026,8,3),"received_date":date(2026,8,1)}],
        [], date(2026,8,4), Decimal("1"), "kg")
    assert expired["expired_quantity"] == "3"


def test_shelf_life_policy_reports_moq_and_pack_forced_overbuy():
    service = ProcurementPlanningService(SimpleNamespace())
    demands = _shelf_demands([3, 3])
    baseline = _empty_baseline(service, demands)
    moq_result = service._apply_shelf_life_policy("shelf-item", "kg", demands, [], [], baseline,
        _shelf_term(moq="10", pack="5"), date(2026, 8, 4), Decimal("10"), 2, Decimal("0"), None, 5)
    assert moq_result[2]["decision"] == "forced_overbuy"
    assert moq_result[2]["quantity_at_risk"] == "4"
    assert set(moq_result[3]) == {"SHELF_LIFE_OVERBUY_RISK", "MOQ_FORCED_OVERBUY"}

    pack_result = service._apply_shelf_life_policy("shelf-item", "kg", demands, [], [], baseline,
        _shelf_term(moq="5", pack="5"), date(2026, 8, 4), Decimal("10"), 2, Decimal("0"), None, 5)
    assert pack_result[2]["decision"] == "forced_overbuy"
    assert set(pack_result[4]) == {"SHELF_LIFE_OVERBUY_RISK", "PACK_SIZE_FORCED_OVERBUY"}


def test_shelf_life_is_evaluated_for_each_supplier_candidate():
    service=ProcurementPlanningService(SimpleNamespace());demands=_shelf_demands([2,2,2,2,2,10])
    baseline=_empty_baseline(service,demands)
    fast=SimpleNamespace(unit="kg",moq=Decimal("5"),pack_size=Decimal("5"),lead_time_days=0,
        unit_cost=20,supplier_id="fast")
    slow=SimpleNamespace(unit="kg",moq=Decimal("5"),pack_size=Decimal("5"),lead_time_days=5,
        unit_cost=1,supplier_id="slow")
    args=(Decimal("20"),"shelf-item","kg",demands,[],[],baseline,date(2026,8,4),Decimal("0"),None,5)
    assert service._shelf_life_candidate_rank(fast,*args) < service._shelf_life_candidate_rank(slow,*args)


def test_ingredients_resolve_independent_shelf_life_policies(session_factory):
    policies = {"banana-policy": 3, "milk-policy": 5, "orange-policy": 7}
    with session_factory() as session:
        demand = []
        for ingredient_id, days in policies.items():
            session.add(IngredientModel(ingredient_id=ingredient_id, store_id="STORE_001", ingredient=ingredient_id,
                normalized_name=f"unique-{ingredient_id}", base_unit="kg", active=True, source="test"))
            supplier_id=f"supplier-{ingredient_id}"
            session.add(SupplierModel(supplier_id=supplier_id, store_id="STORE_001", supplier=supplier_id,
                normalized_name=supplier_id, active=True, source="test")); session.flush()
            session.add(SupplierIngredientTermModel(constraint_id=f"term-{ingredient_id}", store_id="STORE_001",
                supplier_id=supplier_id, ingredient_id=ingredient_id, unit_cost=1, moq=Decimal("1"),
                pack_size=Decimal("1"), lead_time_days=0, unit="kg", version=1, active=True, source="test"))
            session.add(InventoryConstraintModel(constraint_id=f"shelf-{ingredient_id}", store_id="STORE_001",
                ingredient_id=ingredient_id, constraint_type="shelf_life_target", value=Decimal(days), unit="day",
                effective_date=date(2026, 7, 1), version=1, active=True, source="test"))
            demand.append(SimpleNamespace(ingredient_id=ingredient_id, target_date=date(2026, 8, 4), unit="kg",
                p25=Decimal("1"), p50=Decimal("1"), p75=Decimal("1")))
        session.flush()
        plans, _ = ProcurementPlanningService(session).build("STORE_001", SimpleNamespace(cutoff_date=date(2026, 8, 3)),
            demand, ["balanced"], False, 1000)
        assert {key: value["configured_days"] for key, value in plans[0]["shelf_life_trace"].items()} == policies


def test_minimum_stock_uses_max_policy_without_adding_safety(session_factory):
    with session_factory() as session:
        session.add(IngredientModel(ingredient_id="operational-item",store_id="STORE_001",ingredient="Operational item",
            normalized_name="unique-operational-item",base_unit="kg",active=True,source="test"))
        session.add(SupplierModel(supplier_id="operational-supplier",store_id="STORE_001",supplier="Operational supplier",
            normalized_name="unique-operational-supplier",active=True,source="test"));session.flush()
        session.add(SupplierIngredientTermModel(constraint_id="operational-term",store_id="STORE_001",
            supplier_id="operational-supplier",ingredient_id="operational-item",unit_cost=1,moq=Decimal("1"),
            pack_size=Decimal("1"),lead_time_days=0,unit="kg",version=1,active=True,source="test"))
        for kind,value in (("safety_stock",2),("minimum_stock",4)):
            session.add(InventoryConstraintModel(constraint_id=f"operational-{kind}",store_id="STORE_001",
                ingredient_id="operational-item",constraint_type=kind,value=Decimal(value),unit="kg",
                effective_date=date(2026,7,1),version=1,active=True,source="test"))
        session.flush();forecast=SimpleNamespace(cutoff_date=date(2026,8,3))
        demand=[SimpleNamespace(ingredient_id="operational-item",target_date=date(2026,8,4),unit="kg",
            p25=Decimal("10"),p50=Decimal("10"),p75=Decimal("10"))]
        plans,_=ProcurementPlanningService(session).build("STORE_001",forecast,demand,["balanced"],False,1000)
        trace=plans[0]["constraint_trace"]["operational-item"]
        assert trace["target_ending_inventory"] == "4.000000"
        assert plans[0]["lines"][0]["raw_required_quantity"] == Decimal("14.000000")
        assert "MINIMUM_STOCK_GAP" in plans[0]["lines"][0]["reason_codes"]
        session.get(InventoryConstraintModel,"operational-safety_stock").value=Decimal("6");session.flush()
        safety_dominates,_=ProcurementPlanningService(session).build("STORE_001",forecast,demand,["balanced"],False,1000)
        assert Decimal(safety_dominates[0]["constraint_trace"]["operational-item"]["target_ending_inventory"]) == 6
        assert safety_dominates[0]["lines"][0]["raw_required_quantity"] == Decimal("16")


def test_reorder_point_trigger_dates():
    baseline={"daily":[
        {"date":"2026-08-04","ending_inventory":"8"},
        {"date":"2026-08-05","ending_inventory":"5"},
        {"date":"2026-08-06","ending_inventory":"2"}]}
    trigger,current=ProcurementPlanningService._reorder_trigger(baseline,[{"quantity":"10"}],Decimal("5"),date(2026,8,3))
    assert trigger == date(2026,8,5) and current is False
    assert ProcurementPlanningService._reorder_trigger(baseline,[{"quantity":"4"}],Decimal("5"),date(2026,8,3)) == (date(2026,8,3),True)
    assert ProcurementPlanningService._reorder_trigger(baseline,[{"quantity":"10"}],Decimal("1"),date(2026,8,3)) == (None,False)


def test_reorder_and_service_level_affect_trace_warning_and_feasibility(session_factory):
    with session_factory() as session:
        session.add(IngredientModel(ingredient_id="service-item",store_id="STORE_001",ingredient="Service item",
            normalized_name="unique-service-item",base_unit="kg",active=True,source="test"))
        session.add(SupplierModel(supplier_id="late-supplier",store_id="STORE_001",supplier="Late supplier",
            normalized_name="unique-late-supplier",active=True,source="test"));session.flush()
        session.add(SupplierIngredientTermModel(constraint_id="late-term",store_id="STORE_001",supplier_id="late-supplier",
            ingredient_id="service-item",unit_cost=1,moq=Decimal("1"),pack_size=Decimal("1"),lead_time_days=10,
            unit="kg",version=1,active=True,source="test"))
        session.add(InventoryConstraintModel(constraint_id="service-reorder",store_id="STORE_001",ingredient_id="service-item",
            constraint_type="reorder_point",value=Decimal("2"),unit="kg",effective_date=date(2026,7,1),version=1,active=True,source="test"))
        session.add(InventoryConstraintModel(constraint_id="store-service",store_id="STORE_001",ingredient_id=None,
            constraint_type="service_level_target",value=Decimal("0.95"),unit="ratio",effective_date=date(2026,7,1),version=1,active=True,source="test"));session.flush()
        demand=[SimpleNamespace(ingredient_id="service-item",target_date=date(2026,8,4),unit="kg",
            p25=Decimal("5"),p50=Decimal("5"),p75=Decimal("5"))]
        plans,_=ProcurementPlanningService(session).build("STORE_001",SimpleNamespace(cutoff_date=date(2026,8,3)),
            demand,["balanced"],False,1000,"explicit")
        plan=plans[0];trace=plan["constraint_trace"]["service-item"]
        assert trace["reorder_trigger_date"] == "2026-08-03"
        assert Decimal(trace["target_service_level"]) == Decimal("0.95") and Decimal(trace["achieved_fill_rate"]) == 0
        assert trace["strategy_source"] == "explicit"
        assert {"REORDER_POINT_TRIGGERED","SUPPLIER_LEAD_TIME"}.issubset(plan["lines"][0]["reason_codes"])
        assert "URGENT_STOCKOUT_RISK" in plan["lines"][0]["warnings"]
        assert "SERVICE_LEVEL_NOT_MET" in plan["warnings"]
        assert any(item["code"]=="SERVICE_LEVEL_NOT_MET" for item in plan["constraint_violations"])
        assert plan["is_feasible"] is False
        session.get(SupplierIngredientTermModel,"late-term").lead_time_days=0;session.flush()
        achieved,_=ProcurementPlanningService(session).build("STORE_001",SimpleNamespace(cutoff_date=date(2026,8,3)),
            demand,["balanced"],False,1000,"explicit")
        achieved_plan=achieved[0]
        assert Decimal(achieved_plan["constraint_trace"]["service-item"]["achieved_fill_rate"]) == 1
        assert "SERVICE_LEVEL_NOT_MET" not in achieved_plan["warnings"]
        assert not any(item["code"]=="SERVICE_LEVEL_NOT_MET" for item in achieved_plan["constraint_violations"])
