from datetime import date
from decimal import Decimal

import pytest

from app.models.business import IngredientModel, InventoryLotModel
from app.models.store import StoreModel
from app.services.ingredient_expiry import IngredientExpiryClassificationService
from shelfcash_core.exceptions import UnknownExpiryError
from shelfcash_core.inventory.contracts import InventoryDemandLine, InventoryDemandScenario, InventoryLot, InventorySimulationPolicy, PlannedInboundDelivery
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios


def _ingredient(identifier, name, *, mode="unknown", source="inferred"):
    return IngredientModel(ingredient_id=identifier, store_id="store", ingredient=name,
        normalized_name=name.lower(), base_unit="cái", expiry_tracking_mode=mode,
        expiry_tracking_source=source, source="test", version=1)


def _lot(identifier, ingredient_id, expiry=None):
    return InventoryLotModel(lot_id=identifier, store_id="store", ingredient_id=ingredient_id,
        batch_code=identifier, expiry_date=expiry, initial_quantity=Decimal("1"), unit="cái",
        source="test", version=1, received_date_status="unknown")


def test_inference_uses_canonical_ingredient_ids_and_ratio_boundary(session_factory):
    with session_factory() as session:
        session.add(StoreModel(store_id="store", store_name="Store"))
        session.add_all([_ingredient("milk", "Sữa tươi"), _ingredient("orange", "Cam"),
                         _ingredient("banana", "Chuối"), _ingredient("cup", "Ly nhựa")])
        # Five batches still constitute exactly one canonical ingredient.
        session.add_all([_lot(f"milk-{n}", "milk", date(2026, 8, 25) if n == 0 else None) for n in range(5)])
        warnings = IngredientExpiryClassificationService(session).recompute("store")
        values = {item.ingredient_id: item.expiry_tracking_mode for item in session.query(IngredientModel)}
        assert values["milk"] == "required"
        # candidates: cam, chuối, ly = 3/4: global guard leaves cup unknown.
        assert values["cup"] == "unknown"
        assert any(item["code"] == "EXCESSIVE_NON_EXPIRY_INGREDIENT_RATIO" for item in warnings)


def test_non_perishable_inference_at_exactly_half(session_factory):
    with session_factory() as session:
        session.add(StoreModel(store_id="store", store_name="Store"))
        session.add_all([_ingredient("milk", "Sữa tươi"), _ingredient("orange", "Cam"),
                         _ingredient("cup", "Ly nhựa"), _ingredient("straw", "Ống hút")])
        session.add_all([_lot("milk-a", "milk", date(2026, 8, 25)), _lot("orange-a", "orange", date(2026, 8, 25))])
        warnings = IngredientExpiryClassificationService(session).recompute("store")
        values = {item.ingredient_id: item for item in session.query(IngredientModel)}
        assert not any(item["code"] == "EXCESSIVE_NON_EXPIRY_INGREDIENT_RATIO" for item in warnings)
        assert values["cup"].expiry_tracking_mode == "not_required"
        assert values["cup"].expiry_tracking_source == "inferred"
        assert values["straw"].expiry_tracking_mode == "not_required"


def test_declared_not_required_is_never_overridden(session_factory):
    with session_factory() as session:
        session.add(StoreModel(store_id="store", store_name="Store"))
        session.add(_ingredient("cup", "Ly nhựa", mode="not_required", source="declared"))
        session.add(_lot("cup-lot", "cup", date(2026, 8, 25)))
        IngredientExpiryClassificationService(session).recompute("store")
        cup = session.get(IngredientModel, "cup")
        assert cup.expiry_tracking_mode == "not_required"
        assert cup.expiry_tracking_source == "declared"


def test_food_without_evidence_remains_unknown(session_factory):
    with session_factory() as session:
        session.add(StoreModel(store_id="store", store_name="Store"))
        session.add(_ingredient("milk", "Sữa tươi"))
        IngredientExpiryClassificationService(session).recompute("store")
        assert session.get(IngredientModel, "milk").expiry_tracking_mode == "unknown"


def _scenario(quantity=2):
    return InventoryDemandScenario(scenario_id="base", simulation_start_date=date(2026, 8, 6), simulation_end_date=date(2026, 8, 6), lines=[
        InventoryDemandLine(scenario_id="base", store_id="store", ingredient_id="cup", target_date=date(2026, 8, 6), quantity=quantity, unit="cái")])


def test_non_perishable_is_fifo_and_does_not_emit_expiry_warnings():
    lots = [InventoryLot(lot_id="a", store_id="store", ingredient_id="cup", quantity_remaining=1, unit="cái", received_date=date(2026, 8, 1), expiry_tracking_mode="not_required"),
            InventoryLot(lot_id="b", store_id="store", ingredient_id="cup", quantity_remaining=1, unit="cái", received_date=date(2026, 8, 5), expiry_tracking_mode="not_required")]
    result = simulate_inventory_scenarios(lots, [_scenario()], policy=InventorySimulationPolicy(unknown_expiry="reject"), simulation_start_date=date(2026, 8, 6), simulation_end_date=date(2026, 8, 6))
    assert [item.lot_id for item in result.results[0].consumption_traces] == ["a", "b"]
    assert "UNKNOWN_EXPIRY_PLACED_LAST" not in result.warnings


def test_unknown_stays_strict_but_not_required_planned_purchase_does_not_warn():
    unknown = InventoryLot(lot_id="unknown", store_id="store", ingredient_id="cup", quantity_remaining=1, unit="cái")
    with pytest.raises(UnknownExpiryError):
        simulate_inventory_scenarios([unknown], [_scenario(1)], policy=InventorySimulationPolicy(unknown_expiry="reject"), simulation_start_date=date(2026, 8, 6), simulation_end_date=date(2026, 8, 6))
    planned = PlannedInboundDelivery(delivery_id="d", lot_id="p", purchase_order_id="po", source_plan_id="plan", supplier_id="s", store_id="store", ingredient_id="cup", quantity=1, unit="cái", arrival_date=date(2026, 8, 6), expiry_tracking_mode="not_required")
    result = simulate_inventory_scenarios([], [_scenario(1)], [planned], policy=InventorySimulationPolicy(unknown_expiry="reject"), simulation_start_date=date(2026, 8, 6), simulation_end_date=date(2026, 8, 6))
    assert "PLANNED_PURCHASE_SHELF_LIFE_NOT_CONFIGURED" not in result.warnings
