from copy import deepcopy
from datetime import date
from decimal import Decimal

D=Decimal


class InventorySimulationService:
    """Deterministic daily lot simulation; inbound arrives before expiry/FEFO consumption."""
    def simulate(self, ingredient_id, unit, demands, lots, inbound=None, reserved=D(0)):
        working=deepcopy(lots); inbound=inbound or []
        for lot in working: lot["quantity"]=D(lot["quantity"])
        if reserved:
            remaining=D(reserved)
            for lot in sorted(working,key=self._fefo):
                take=min(lot["quantity"],remaining);lot["quantity"]-=take;remaining-=take
        daily=[];total_demand=D(0);total_fulfilled=D(0);total_shortage=D(0);total_expired=D(0);received_events=set()
        first_shortage=None
        for demand_row in sorted(demands,key=lambda x:x["date"]):
            day=demand_row["date"]; opening=sum((x["quantity"] for x in working),D(0));inbound_qty=D(0)
            for index,event in enumerate(inbound):
                if index in received_events or event["date"]>day:continue
                qty=D(event["quantity"]);inbound_qty+=qty
                working.append({"lot_id":event.get("lot_id",f"inbound:{len(working)}"),"quantity":qty,
                    "expiry_date":event.get("expiry_date"),"received_date":day})
                received_events.add(index)
            expired=D(0)
            for lot in working:
                if lot.get("expiry_date") is not None and lot["expiry_date"]<day and lot["quantity"]>0:
                    expired+=lot["quantity"];lot["quantity"]=D(0)
            demand=D(demand_row["quantity"]);remaining=demand;consumed=[]
            for lot in sorted(working,key=self._fefo):
                if remaining<=0:break
                take=min(lot["quantity"],remaining)
                if take: lot["quantity"]-=take;remaining-=take;consumed.append({"lot_id":lot["lot_id"],"quantity":str(take)})
            fulfilled=demand-remaining;ending=sum((x["quantity"] for x in working),D(0));shortage=remaining
            if shortage>0 and first_shortage is None:first_shortage=day
            total_demand+=demand;total_fulfilled+=fulfilled;total_shortage+=shortage;total_expired+=expired
            daily.append({"ingredient_id":ingredient_id,"date":day.isoformat(),"unit":unit,
                "opening_inventory":str(opening),"inbound_quantity":str(inbound_qty),"demand_quantity":str(demand),
                "fulfilled_quantity":str(fulfilled),"shortage_quantity":str(shortage),"expired_quantity":str(expired),
                "waste_quantity":str(expired),"ending_inventory":str(ending),"consumed_lots":consumed})
        ending=sum((x["quantity"] for x in working),D(0));fill=D(1) if total_demand==0 else total_fulfilled/total_demand
        at_risk=sum((x["quantity"] for x in working if x.get("expiry_date") and demands and x["expiry_date"]<=max(d["date"] for d in demands)),D(0))
        avg=(total_demand/D(len(demands))) if demands else D(0)
        return {"ingredient_id":ingredient_id,"unit":unit,"opening_inventory":daily[0]["opening_inventory"] if daily else "0",
            "inbound_quantity":str(sum((D(x["quantity"]) for x in inbound),D(0))),"demand_quantity":str(total_demand),
            "fulfilled_quantity":str(total_fulfilled),"shortage_quantity":str(total_shortage),"expired_quantity":str(total_expired),
            "waste_quantity":str(total_expired),"ending_inventory":str(ending),"days_of_supply":str(ending/avg) if avg>0 else None,
            "projected_stockout_date":first_shortage.isoformat() if first_shortage else None,
            "first_shortage_date":first_shortage.isoformat() if first_shortage else None,"at_risk_expiry_quantity":str(at_risk),
            "fill_rate":str(fill),"daily":daily}

    @staticmethod
    def _fefo(lot):
        received = lot.get("received_date")
        return (lot.get("expiry_date") or date.max, 1 if received is None else 0,
                received or date.max, str(lot.get("lot_id")))
