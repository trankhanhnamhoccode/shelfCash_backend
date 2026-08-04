import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func, select

from app.core.exceptions import PlanningError, ValidationError
from app.core.units import convert_quantity
from app.models.business import (InventoryLotModel, InventoryMovementModel, StoreSettingsModel,
    SupplierIngredientTermModel, SupplierModel, IngredientModel)
from app.models.operations import BudgetPeriodModel, PurchaseOrderLineModel, PurchaseOrderModel
from app.services.inventory_simulation_service import InventorySimulationService
from app.services.business_constraint_resolver import BusinessConstraintResolver

D=Decimal
SCENARIOS={"lean":"p25","balanced":"p50","protected":"p75"}


class ProcurementPlanningService:
    def __init__(self,session):self.session=session;self.simulator=InventorySimulationService();self.constraints=BusinessConstraintResolver(session)

    def build(self,store_id,forecast,demand_rows,strategies,use_open_purchase_orders=True,budget_override=None):
        by_ingredient=defaultdict(list)
        for row in demand_rows:by_ingredient[row.ingredient_id].append(row)
        inventory={ingredient:self._lots(store_id,ingredient,forecast.cutoff_date) for ingredient in by_ingredient}
        existing_inbound=self._open_inbound(store_id,forecast.cutoff_date) if use_open_purchase_orders else defaultdict(list)
        budget,warnings=self._budget(store_id,forecast.cutoff_date,budget_override)
        plans=[]
        for strategy in strategies:
            quantile=SCENARIOS[strategy];lines=[];proposed=defaultdict(list);baseline={};violations=[];plan_warnings=list(warnings);constraint_trace={}
            for ingredient_id,rows in by_ingredient.items():
                unit=rows[0].unit;demands=[{"date":x.target_date,"quantity":D(getattr(x,quantile))} for x in rows]
                baseline[ingredient_id]=self.simulator.simulate(ingredient_id,unit,demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[]))
                terms=self._terms(store_id,ingredient_id)
                term=self._select_term(terms,forecast.cutoff_date,baseline[ingredient_id].get("first_shortage_date"))
                configured_safety=self.constraints.resolve_quantity(store_id,"safety_stock",ingredient_id,unit,forecast.cutoff_date)
                safety=D(0) if configured_safety is None else D(configured_safety)
                maximum=self.constraints.resolve_quantity(store_id,"maximum_stock",ingredient_id,unit,forecast.cutoff_date)
                fallback="ZERO_WITH_WARNING" if configured_safety is None else None
                if configured_safety is None: plan_warnings.append("SAFETY_STOCK_NOT_CONFIGURED")
                constraint_trace[ingredient_id]={"configured_safety_stock":None if configured_safety is None else str(configured_safety),
                    "effective_safety_stock":str(safety),"fallback_policy":fallback,
                    "maximum_stock":None if maximum is None else str(maximum),"unit":unit}
                raw=max(D(0),D(baseline[ingredient_id]["shortage_quantity"])+max(D(0),safety-D(baseline[ingredient_id]["ending_inventory"])))
                if maximum is not None:
                    raw=min(raw,max(D(0),D(maximum)-D(baseline[ingredient_id]["ending_inventory"])))
                reasons=[];line_warnings=[]
                if D(baseline[ingredient_id]["shortage_quantity"])>0:reasons.append("PROJECTED_STOCKOUT")
                if safety>D(baseline[ingredient_id]["ending_inventory"]):reasons.append("SAFETY_STOCK_GAP")
                if D(baseline[ingredient_id]["expired_quantity"])>0:reasons.append("EXPIRY_REPLACEMENT")
                if term is None:
                    if raw>0:violations.append({"code":"SUPPLIER_TERM_NOT_FOUND","ingredient_id":ingredient_id})
                    lines.append(self._empty_line(ingredient_id,forecast.cutoff_date,unit,raw,reasons,["SUPPLIER_TERM_NOT_FOUND"]));continue
                try:raw_term=convert_quantity(raw,unit,term.unit)
                except ValidationError as exc:raise PlanningError("SUPPLIER_TERM_INVALID","Supplier unit không tương thích.",exc.details) from exc
                order=D(0);packs=0
                if raw_term>0:
                    packs=int((raw_term/D(term.pack_size)).to_integral_value(rounding=ROUND_CEILING));order=D(packs)*D(term.pack_size)
                    if order<D(term.moq):
                        packs=int((D(term.moq)/D(term.pack_size)).to_integral_value(rounding=ROUND_CEILING));order=D(packs)*D(term.pack_size);reasons.append("MOQ_ROUNDING")
                    if order>raw_term:reasons.append("PACK_SIZE_ROUNDING")
                arrival=forecast.cutoff_date+timedelta(days=term.lead_time_days)
                first=baseline[ingredient_id].get("first_shortage_date")
                if first and arrival.isoformat()>first:
                    line_warnings.append("URGENT_STOCKOUT_RISK");reasons.append("SUPPLIER_LEAD_TIME")
                inbound_base=convert_quantity(order,term.unit,unit)
                if inbound_base:proposed[ingredient_id].append({"date":arrival,"quantity":inbound_base,"lot_id":f"proposed:{strategy}:{ingredient_id}"})
                cost=int(order*D(term.unit_cost));excess=order-raw_term
                lines.append({"ingredient_id":ingredient_id,"supplier_id":term.supplier_id,"order_date":forecast.cutoff_date,
                    "supplier_term_id":term.constraint_id,
                    "expected_arrival_date":arrival,"raw_required_quantity":raw_term,"order_quantity":order,"unit":term.unit,
                    "pack_count":packs,"unit_cost":term.unit_cost,"line_cost":cost,"moq":D(term.moq),"pack_size":D(term.pack_size),
                    "lead_time_days":term.lead_time_days,"rounding_excess":excess,"reason_codes":reasons,"warnings":line_warnings})
            projections=[];shortage=D(0);waste=D(0);demand=D(0);fulfilled=D(0);expiry_risk=D(0);stockouts=set()
            for ingredient_id,rows in by_ingredient.items():
                unit=rows[0].unit;demands=[{"date":x.target_date,"quantity":D(getattr(x,quantile))} for x in rows]
                sim=self.simulator.simulate(ingredient_id,unit,demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[])+proposed.get(ingredient_id,[]))
                projections.append(sim);shortage+=D(sim["shortage_quantity"]);waste+=D(sim["waste_quantity"]);demand+=D(sim["demand_quantity"]);fulfilled+=D(sim["fulfilled_quantity"]);expiry_risk+=D(sim["at_risk_expiry_quantity"])
                if sim["first_shortage_date"]:stockouts.add(ingredient_id)
                plan_warnings.append("STORAGE_CAPACITY_NOT_CONFIGURED")
            cost=sum(x["line_cost"] for x in lines);budget_violation=[]
            if budget is not None and cost>budget:budget_violation=[{"code":"BUDGET_EXCEEDED","budget":budget,"cost":cost}]
            violations.extend(budget_violation);fill=D(1) if demand==0 else fulfilled/demand
            plans.append({"strategy":strategy,"is_feasible":not violations,"is_recommended":False,"total_purchase_cost":cost,
                "total_order_quantity":sum((D(x["order_quantity"]) for x in lines),D(0)),"projected_shortage_quantity":shortage,
                "projected_waste_quantity":waste,"fill_rate":fill,"stockout_ingredient_count":len(stockouts),
                "expiry_risk_quantity":expiry_risk,"budget_used":cost,"budget_remaining":None if budget is None else budget-cost,
                "constraint_violations":violations,"constraint_trace":constraint_trace,"warnings":sorted(set(plan_warnings)),"lines":lines,"daily_projections":projections})
        recommended=next((x for x in plans if x["strategy"]=="balanced" and x["is_feasible"]),None) or next((x for x in plans if x["is_feasible"]),None)
        if recommended:recommended["is_recommended"]=True
        return plans,recommended["strategy"] if recommended else None

    def _lots(self,store,ingredient,cutoff):
        rows=list(self.session.scalars(select(InventoryLotModel).where(InventoryLotModel.store_id==store,
            InventoryLotModel.ingredient_id==ingredient,InventoryLotModel.received_date<=cutoff)))
        result=[]
        for lot in rows:
            movements=list(self.session.scalars(select(InventoryMovementModel).where(InventoryMovementModel.lot_id==lot.lot_id)))
            balance=sum((D(x.quantity_delta) for x in movements if x.occurred_at.date()<=cutoff),D(0))
            if balance>0:result.append({"lot_id":lot.lot_id,"quantity":balance,"expiry_date":lot.expiry_date,"received_date":lot.received_date})
        return result

    def _open_inbound(self,store,cutoff):
        result=defaultdict(list)
        rows=self.session.execute(select(PurchaseOrderModel,PurchaseOrderLineModel).join(PurchaseOrderLineModel,
            PurchaseOrderLineModel.po_id==PurchaseOrderModel.po_id).where(PurchaseOrderModel.store_id==store,
            PurchaseOrderModel.status.in_(["ordered","partially_received"]),PurchaseOrderModel.delivery_date>cutoff)).all()
        for po,line in rows:
            remaining=D(line.ordered_quantity)-D(line.received_quantity)
            ingredient=self.session.get(IngredientModel,line.ingredient_id)
            if remaining>0 and ingredient:
                try:quantity=convert_quantity(remaining,line.unit,ingredient.base_unit)
                except ValidationError as exc:raise PlanningError("INVENTORY_LOT_UNIT_INVALID","Open PO unit không tương thích.",exc.details) from exc
                result[line.ingredient_id].append({"date":po.delivery_date,"quantity":quantity,"lot_id":f"po:{po.po_id}:{line.po_line_id}"})
        return result

    def _terms(self,store,ingredient):return list(self.session.scalars(select(SupplierIngredientTermModel).join(SupplierModel).where(
        SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.ingredient_id==ingredient,
        SupplierIngredientTermModel.active.is_(True),SupplierModel.active.is_(True))))
    @staticmethod
    def _select_term(terms,cutoff,need):
        return sorted(terms,key=lambda x:(cutoff+timedelta(days=x.lead_time_days)> (__import__('datetime').date.fromisoformat(need) if need else __import__('datetime').date.max),x.unit_cost,x.lead_time_days,x.supplier_id))[0] if terms else None
    def _budget(self,store,cutoff,override):
        if override is not None:return int(override),[]
        period=cutoff.strftime("%Y-%m");bp=self.session.scalar(select(BudgetPeriodModel).where(BudgetPeriodModel.store_id==store,BudgetPeriodModel.period==period))
        if bp and bp.monthly_budget>0:return bp.monthly_budget-bp.reserved_budget-bp.spent_budget,[]
        settings=self.session.scalar(select(StoreSettingsModel).where(StoreSettingsModel.store_id==store))
        if settings and settings.monthly_budget>0:return settings.monthly_budget,[]
        return None,["BUDGET_NOT_CONFIGURED","RESERVED_INVENTORY_NOT_AVAILABLE"]
    @staticmethod
    def _empty_line(ingredient,day,unit,raw,reasons,warnings):return {"ingredient_id":ingredient,"supplier_id":None,"order_date":day,"expected_arrival_date":None,
        "supplier_term_id":None,
        "raw_required_quantity":raw,"order_quantity":D(0),"unit":unit,"pack_count":None,"unit_cost":None,"line_cost":0,"moq":None,"pack_size":None,
        "lead_time_days":None,"rounding_excess":D(0),"reason_codes":reasons,"warnings":warnings}
