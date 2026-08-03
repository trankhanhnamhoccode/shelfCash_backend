from datetime import date
from decimal import Decimal

from app.services.inventory_simulation_service import InventorySimulationService


def test_fefo_expiry_inbound_shortage_and_daily_balance():
    service=InventorySimulationService()
    result=service.simulate("milk","kg",[
        {"date":date(2026,8,4),"quantity":Decimal("6")},
        {"date":date(2026,8,5),"quantity":Decimal("8")},
    ],[
        {"lot_id":"late","quantity":Decimal("5"),"expiry_date":date(2026,8,20),"received_date":date(2026,8,1)},
        {"lot_id":"early","quantity":Decimal("4"),"expiry_date":date(2026,8,5),"received_date":date(2026,8,1)},
        {"lot_id":"expired","quantity":Decimal("2"),"expiry_date":date(2026,8,3),"received_date":date(2026,7,1)},
    ],[{"date":date(2026,8,5),"quantity":Decimal("3"),"lot_id":"inbound"}])
    assert result["daily"][0]["consumed_lots"][0]["lot_id"] == "early"
    assert Decimal(result["expired_quantity"]) == 2
    assert Decimal(result["shortage_quantity"]) == 2
    assert result["first_shortage_date"] == "2026-08-05"
    for day in result["daily"]:
        assert Decimal(day["opening_inventory"])+Decimal(day["inbound_quantity"])-Decimal(day["expired_quantity"])-Decimal(day["fulfilled_quantity"])==Decimal(day["ending_inventory"])


def test_reserved_inventory_is_not_available_and_scenarios_are_independent():
    service=InventorySimulationService();lots=[{"lot_id":"one","quantity":Decimal("10"),"expiry_date":None,"received_date":date(2026,8,1)}]
    low=service.simulate("x","kg",[{"date":date(2026,8,2),"quantity":Decimal("4")}],lots,reserved=Decimal("3"))
    high=service.simulate("x","kg",[{"date":date(2026,8,2),"quantity":Decimal("9")}],lots,reserved=Decimal("3"))
    assert Decimal(low["ending_inventory"])==3 and Decimal(high["shortage_quantity"])==2
    assert lots[0]["quantity"]==10
