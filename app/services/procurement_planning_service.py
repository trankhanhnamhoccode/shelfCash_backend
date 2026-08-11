import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func, select

from app.core.exceptions import PlanningError, ValidationError
from app.core.units import canonical_unit_name, convert_quantity, normalize_unit, unit_dimension
from app.models.business import (InventoryLotModel, InventoryMovementModel,
    SupplierIngredientTermModel, SupplierModel, IngredientModel)
from app.models.operations import PurchaseOrderLineModel, PurchaseOrderModel
from app.services.budget_resolver import BudgetResolver
from app.services.inventory_simulation_service import InventorySimulationService
from app.services.business_constraint_resolver import BusinessConstraintResolver

D=Decimal
SCENARIOS={"lean":"p25","balanced":"p50","protected":"p75"}


class ProcurementPlanningService:
    def __init__(self,session):self.session=session;self.simulator=InventorySimulationService();self.constraints=BusinessConstraintResolver(session)

    def build(self,store_id,forecast,demand_rows,strategies,use_open_purchase_orders=True,budget_override=None,strategy_source="explicit"):
        by_ingredient=defaultdict(list)
        for row in demand_rows:by_ingredient[row.ingredient_id].append(row)
        inventory={ingredient:self._lots(store_id,ingredient,forecast.cutoff_date) for ingredient in by_ingredient}
        existing_inbound=self._open_inbound(store_id,forecast.cutoff_date) if use_open_purchase_orders else defaultdict(list)
        horizon_end=max((row.target_date for row in demand_rows),default=forecast.cutoff_date)
        resolved_budget=BudgetResolver(self.session).resolve(store_id,forecast.cutoff_date,budget_override,horizon_end)
        budget=resolved_budget.limit;warnings=[] if budget is not None else ["BUDGET_NOT_CONFIGURED","RESERVED_INVENTORY_NOT_AVAILABLE"]
        capacity_constraint=self.constraints.resolve_storage_capacity(store_id,forecast.cutoff_date)
        plans=[]
        for strategy in strategies:
            quantile=SCENARIOS[strategy];lines=[];proposed=defaultdict(list);baseline={};violations=[];plan_warnings=list(warnings);constraint_trace={};shelf_life_trace={}
            for ingredient_id,rows in by_ingredient.items():
                unit=rows[0].unit;demands=[{"date":x.target_date,"quantity":D(getattr(x,quantile))} for x in rows]
                baseline[ingredient_id]=self.simulator.simulate(ingredient_id,unit,demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[]))
                terms=self._terms(store_id,ingredient_id)
                term=self._select_term(terms,forecast.cutoff_date,baseline[ingredient_id].get("first_shortage_date"))
                configured_safety=self.constraints.resolve_quantity(store_id,"safety_stock",ingredient_id,unit,forecast.cutoff_date)
                safety=D(0) if configured_safety is None else D(configured_safety)
                maximum=self.constraints.resolve_quantity(store_id,"maximum_stock",ingredient_id,unit,forecast.cutoff_date)
                minimum=self.constraints.resolve_quantity(store_id,"minimum_stock",ingredient_id,unit,forecast.cutoff_date)
                reorder_point=self.constraints.resolve_quantity(store_id,"reorder_point",ingredient_id,unit,forecast.cutoff_date)
                service_level=self._resolve_service_level(store_id,ingredient_id,forecast.cutoff_date)
                shelf_life_days=self.constraints.resolve_duration_days(store_id,"shelf_life_target",ingredient_id,forecast.cutoff_date)
                target_ending=max(safety,D(0) if minimum is None else D(minimum))
                reorder_trigger,current_reorder_triggered=self._reorder_trigger(baseline[ingredient_id],inventory[ingredient_id],reorder_point,forecast.cutoff_date)
                fallback="ZERO_WITH_WARNING" if configured_safety is None else None
                if configured_safety is None: plan_warnings.append("SAFETY_STOCK_NOT_CONFIGURED")
                constraint_trace[ingredient_id]={"configured_safety_stock":None if configured_safety is None else str(configured_safety),
                    "effective_safety_stock":str(safety),"fallback_policy":fallback,
                    "maximum_stock":None if maximum is None else str(maximum),
                    "minimum_stock":None if minimum is None else str(minimum),"target_ending_inventory":str(target_ending),
                    "target_ending_policy":"MAX_SAFETY_AND_MINIMUM","reorder_point":None if reorder_point is None else str(reorder_point),
                    "reorder_trigger_date":None if reorder_trigger is None else reorder_trigger.isoformat(),
                    "target_service_level":None if service_level is None else str(service_level),
                    "achieved_fill_rate":None,"strategy_source":strategy_source,"unit":unit}
                raw=max(D(0),D(baseline[ingredient_id]["shortage_quantity"])+max(D(0),target_ending-D(baseline[ingredient_id]["ending_inventory"])))
                if maximum is not None:
                    raw=min(raw,max(D(0),D(maximum)-D(baseline[ingredient_id]["ending_inventory"])))
                reasons=[];line_warnings=[]
                if D(baseline[ingredient_id]["shortage_quantity"])>0:reasons.append("PROJECTED_STOCKOUT")
                if safety>D(baseline[ingredient_id]["ending_inventory"]):reasons.append("SAFETY_STOCK_GAP")
                if minimum is not None and D(minimum)>D(baseline[ingredient_id]["ending_inventory"]):reasons.append("MINIMUM_STOCK_GAP")
                if current_reorder_triggered:reasons.append("REORDER_POINT_TRIGGERED")
                if D(baseline[ingredient_id]["expired_quantity"])>0:reasons.append("EXPIRY_REPLACEMENT")
                if shelf_life_days is not None and terms and raw>0:
                    term=min(terms,key=lambda candidate:self._shelf_life_candidate_rank(candidate,raw,ingredient_id,unit,
                        demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[]),baseline[ingredient_id],
                        forecast.cutoff_date,safety,minimum,int(shelf_life_days)))
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
                arrival,delivery_adjusted=self._delivery_date(term,forecast.cutoff_date)
                if arrival is None:
                    line_warnings.append("SUPPLIER_DELIVERY_UNAVAILABLE");reasons.append("SUPPLIER_DELIVERY_UNAVAILABLE")
                    violations.append({"code":"SUPPLIER_DELIVERY_UNAVAILABLE","ingredient_id":ingredient_id,"supplier_id":term.supplier_id})
                elif delivery_adjusted:
                    reasons.append("DELIVERY_DAY_ADJUSTMENT");line_warnings.append("DELIVERY_DAY_ADJUSTMENT")
                first=baseline[ingredient_id].get("first_shortage_date")
                required_by=min((date.fromisoformat(first) if first else date.max),(reorder_trigger or date.max))
                if arrival is not None and arrival>required_by:
                    line_warnings.append("URGENT_STOCKOUT_RISK");reasons.append("SUPPLIER_LEAD_TIME")
                if shelf_life_days is not None and order>0 and arrival is not None:
                    order,packs,trace,shelf_reasons,shelf_warnings=self._apply_shelf_life_policy(
                        ingredient_id,unit,demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[]),
                        baseline[ingredient_id],term,arrival,order,packs,safety,minimum,int(shelf_life_days))
                    shelf_life_trace[ingredient_id]=trace;reasons.extend(shelf_reasons);line_warnings.extend(shelf_warnings)
                inbound_base=convert_quantity(order,term.unit,unit)
                if inbound_base and arrival is not None:proposed[ingredient_id].append({"date":arrival,"quantity":inbound_base,"lot_id":f"proposed:{strategy}:{ingredient_id}"})
                cost=int(order*D(term.unit_cost));excess=order-raw_term
                lines.append({"ingredient_id":ingredient_id,"supplier_id":term.supplier_id,"order_date":min(forecast.cutoff_date,reorder_trigger or forecast.cutoff_date),
                    "supplier_term_id":term.constraint_id,
                    "expected_arrival_date":arrival,"raw_required_quantity":raw_term,"order_quantity":order,"unit":term.unit,
                    "pack_count":packs,"unit_cost":term.unit_cost,"line_cost":cost,"moq":D(term.moq),"pack_size":D(term.pack_size),
                    "lead_time_days":term.lead_time_days,"rounding_excess":excess,"reason_codes":reasons,"warnings":line_warnings})
            projections=[];shortage=D(0);waste=D(0);demand=D(0);fulfilled=D(0);expiry_risk=D(0);stockouts=set()
            for ingredient_id,rows in by_ingredient.items():
                unit=rows[0].unit;demands=[{"date":x.target_date,"quantity":D(getattr(x,quantile))} for x in rows]
                sim=self.simulator.simulate(ingredient_id,unit,demands,inventory[ingredient_id],existing_inbound.get(ingredient_id,[])+proposed.get(ingredient_id,[]))
                projections.append(sim);shortage+=D(sim["shortage_quantity"]);waste+=D(sim["waste_quantity"]);demand+=D(sim["demand_quantity"]);fulfilled+=D(sim["fulfilled_quantity"]);expiry_risk+=D(sim["at_risk_expiry_quantity"])
                ingredient_fill=D(sim["fill_rate"]);trace=constraint_trace[ingredient_id];trace["achieved_fill_rate"]=str(ingredient_fill)
                target=trace["target_service_level"]
                if target is not None and ingredient_fill<D(target):
                    violations.append({"code":"SERVICE_LEVEL_NOT_MET","ingredient_id":ingredient_id,
                        "target_service_level":target,"achieved_fill_rate":str(ingredient_fill)})
                    plan_warnings.append("SERVICE_LEVEL_NOT_MET")
                if sim["first_shortage_date"]:stockouts.add(ingredient_id)
            capacity_trace,capacity_warning,capacity_violation=self._evaluate_storage_capacity(capacity_constraint,projections)
            if capacity_warning:plan_warnings.append(capacity_warning)
            if capacity_violation:violations.append(capacity_violation)
            cost=sum(x["line_cost"] for x in lines);budget_violation=[]
            if budget is not None and cost>budget:budget_violation=[{"code":"BUDGET_EXCEEDED","budget":budget,"cost":cost}]
            violations.extend(budget_violation);fill=D(1) if demand==0 else fulfilled/demand
            plans.append({"strategy":strategy,"is_feasible":not violations,"is_recommended":False,"total_purchase_cost":cost,
                "total_order_quantity":sum((D(x["order_quantity"]) for x in lines),D(0)),"projected_shortage_quantity":shortage,
                "projected_waste_quantity":waste,"fill_rate":fill,"stockout_ingredient_count":len(stockouts),
                "expiry_risk_quantity":expiry_risk,"budget_used":cost,"budget_remaining":None if budget is None else budget-cost,
                "constraint_violations":violations,"constraint_trace":constraint_trace,"budget_trace":resolved_budget.trace,"shelf_life_trace":shelf_life_trace,"storage_capacity_trace":capacity_trace,
                "strategy_source":strategy_source,"warnings":sorted(set(plan_warnings)),"lines":lines,"daily_projections":projections})
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
                result[line.ingredient_id].append({"date":po.delivery_date,"quantity":quantity,"lot_id":f"po:{po.po_id}:{line.po_line_id}","shelf_life_days":line.shelf_life_days})
        return result

    def _terms(self,store,ingredient):return list(self.session.scalars(select(SupplierIngredientTermModel).join(SupplierModel).where(
        SupplierIngredientTermModel.store_id==store,SupplierIngredientTermModel.ingredient_id==ingredient,
        SupplierIngredientTermModel.active.is_(True),SupplierModel.active.is_(True))))
    def _select_term(self,terms,cutoff,need):
        required=date.fromisoformat(need) if need else date.max
        return sorted(terms,key=lambda x:((self._delivery_date(x,cutoff)[0] is None),
            (self._delivery_date(x,cutoff)[0] or date.max)>required,x.unit_cost,x.lead_time_days,x.supplier_id))[0] if terms else None
    @staticmethod
    def _delivery_date(term,order_date):
        nominal=order_date+timedelta(days=term.lead_time_days)
        schedule = getattr(term, "available_delivery_days", None)
        if schedule is None:return nominal,False
        allowed=json.loads(schedule)
        if not allowed:return None,False
        for offset in range(14):
            candidate=nominal+timedelta(days=offset)
            if candidate.weekday() in allowed:return candidate,offset>0
        return None,False
    def _resolve_service_level(self,store,ingredient,as_of):
        ingredient_target=self.constraints.resolve_ratio(store,"service_level_target",ingredient,as_of)
        return ingredient_target if ingredient_target is not None else self.constraints.resolve_ratio(store,"service_level_target",None,as_of)
    @staticmethod
    def _reorder_trigger(baseline,lots,reorder_point,cutoff):
        if reorder_point is None:return None,False
        current=sum((D(lot["quantity"]) for lot in lots),D(0))
        if current<=D(reorder_point):return cutoff,True
        for row in baseline["daily"]:
            if D(row["ending_inventory"])<=D(reorder_point):return date.fromisoformat(row["date"]),False
        return None,False
    @staticmethod
    def _evaluate_storage_capacity(constraint,projections):
        if constraint is None:
            return ({"configured":False,"constraint_id":None,"constraint_type":None,"configured_value":None,
                "configured_unit":None,"canonical_value":None,"canonical_unit":None,"evaluation_status":"not_configured",
                "reason":"no_effective_capacity_constraint"},"STORAGE_CAPACITY_NOT_CONFIGURED",None)
        try:
            capacity_unit=normalize_unit(constraint.unit);capacity_dimension=unit_dimension(capacity_unit)
            canonical_name=canonical_unit_name(capacity_unit);canonical_value=convert_quantity(constraint.value,constraint.unit,capacity_unit)
        except ValidationError as exc:
            raise PlanningError("BUSINESS_CONSTRAINT_UNIT_CONVERSION_FAILED","Storage capacity unit is invalid.",
                {"constraint_id":constraint.constraint_id,"constraint_type":constraint.constraint_type,"unit":constraint.unit}) from exc
        trace={"configured":True,"constraint_id":constraint.constraint_id,"constraint_type":constraint.constraint_type,
            "configured_value":float(constraint.value),"configured_unit":constraint.unit,"canonical_value":float(canonical_value),
            "canonical_unit":canonical_name,"evaluation_status":None,"reason":None}
        occupied=[];dimensions=set()
        for projection in projections:
            has_inventory=any(max(D(row["opening_inventory"])+D(row["inbound_quantity"]),D(row["ending_inventory"]))>0 for row in projection["daily"])
            if has_inventory:
                dimensions.add(unit_dimension(projection["unit"]));occupied.append(projection)
        if len(dimensions)>1:
            trace.update({"evaluation_status":"dimension_unsupported","reason":"mixed_inventory_dimensions",
                "inventory_dimensions":sorted(dimensions)})
            return trace,"STORAGE_CAPACITY_DIMENSION_UNSUPPORTED",None
        if dimensions and next(iter(dimensions))!=capacity_dimension:
            trace.update({"evaluation_status":"dimension_unsupported","reason":"capacity_dimension_mismatch",
                "inventory_dimensions":sorted(dimensions),"capacity_dimension":capacity_dimension})
            return trace,"STORAGE_CAPACITY_DIMENSION_UNSUPPORTED",None
        totals=defaultdict(D)
        for projection in occupied:
            for row in projection["daily"]:
                peak_for_ingredient=max(D(row["opening_inventory"])+D(row["inbound_quantity"]),D(row["ending_inventory"]))
                totals[row["date"]]+=convert_quantity(peak_for_ingredient,projection["unit"],capacity_unit)
        peak_date,peak_value=(None,D(0)) if not totals else max(totals.items(),key=lambda item:(item[1],-date.fromisoformat(item[0]).toordinal()))
        excess=max(D(0),peak_value-canonical_value)
        trace.update({"evaluation_status":"exceeded" if excess>0 else "within_capacity",
            "reason":"peak_projected_inventory_exceeds_capacity" if excess>0 else None,"peak_date":peak_date,
            "peak_value":float(peak_value),"excess_quantity":float(excess)})
        if excess>0:
            violation={"code":"STORAGE_CAPACITY_EXCEEDED","constraint_id":constraint.constraint_id,
                "constraint_type":constraint.constraint_type,"peak_date":peak_date,"peak_value":str(peak_value),
                "capacity":str(canonical_value),"excess_quantity":str(excess),"unit":canonical_name}
            return trace,"STORAGE_CAPACITY_EXCEEDED",violation
        return trace,None,None
    def _apply_shelf_life_policy(self,ingredient_id,base_unit,demands,lots,existing_inbound,baseline,term,
            arrival,rounded_order,packs,safety,minimum,shelf_life_days):
        window_end=arrival+timedelta(days=shelf_life_days-1)
        demand_base=sum((D(row["quantity"]) for row in demands if arrival<=row["date"]<=window_end),D(0))
        projected_base=self._projected_inventory_at_arrival(baseline,arrival)
        required_floor=max(D(safety),D(0) if minimum is None else D(minimum))
        usable_base=max(D(0),demand_base+required_floor-projected_base)
        demand_term=convert_quantity(demand_base,base_unit,term.unit)
        projected_term=convert_quantity(projected_base,base_unit,term.unit)
        usable_term=convert_quantity(usable_base,base_unit,term.unit)
        original_order=D(rounded_order);pack=D(term.pack_size);moq=D(term.moq)
        original_sim=self._simulate_candidate(ingredient_id,base_unit,demands,lots,existing_inbound,arrival,original_order,term.unit)
        allowed_shortage=self._simulation_value_through(original_sim,"shortage_quantity",window_end,sum)
        selected=original_order;selected_packs=packs
        minimum_packs=int((moq/pack).to_integral_value(rounding=ROUND_CEILING))
        for candidate_packs in range(packs-1,minimum_packs-1,-1):
            candidate=D(candidate_packs)*pack
            simulation=self._simulate_candidate(ingredient_id,base_unit,demands,lots,existing_inbound,arrival,candidate,term.unit)
            shortage=self._simulation_value_through(simulation,"shortage_quantity",window_end,sum)
            ending=self._simulation_value_through(simulation,"ending_inventory",window_end,lambda values:values[-1] if values else D(0))
            if shortage<=allowed_shortage and (minimum is None or ending>=D(minimum)):
                selected=candidate;selected_packs=candidate_packs
        at_risk=max(D(0),selected-usable_term);reasons=[];warnings=[]
        if at_risk>0:
            reasons.append("SHELF_LIFE_OVERBUY_RISK");warnings.append("SHELF_LIFE_OVERBUY_RISK")
            if moq>usable_term:
                reasons.append("MOQ_FORCED_OVERBUY");warnings.append("MOQ_FORCED_OVERBUY")
            if moq<=usable_term:
                reasons.append("PACK_SIZE_FORCED_OVERBUY");warnings.append("PACK_SIZE_FORCED_OVERBUY")
        decision="forced_overbuy" if at_risk>0 else ("reduced" if selected<original_order else "within_limit")
        trace={"configured_days":shelf_life_days,"demand_window_start":arrival.isoformat(),
            "demand_window_end":window_end.isoformat(),"demand_within_window":str(demand_term),
            "projected_inventory_at_arrival":str(projected_term),"effective_safety_stock":str(convert_quantity(safety,base_unit,term.unit)),
            "minimum_stock":None if minimum is None else str(convert_quantity(minimum,base_unit,term.unit)),
            "maximum_usable_replenishment":str(usable_term),"initial_rounded_order_quantity":str(original_order),
            "rounded_order_quantity":str(selected),"quantity_at_risk":str(at_risk),"unit":term.unit,"decision":decision}
        return selected,selected_packs,trace,reasons,warnings
    def _shelf_life_candidate_rank(self,term,raw_base,ingredient_id,base_unit,demands,lots,existing_inbound,
            baseline,cutoff,safety,minimum,shelf_life_days):
        raw_term=convert_quantity(raw_base,base_unit,term.unit);pack=D(term.pack_size);moq=D(term.moq)
        packs=int((raw_term/pack).to_integral_value(rounding=ROUND_CEILING));order=D(packs)*pack
        if order<moq:
            packs=int((moq/pack).to_integral_value(rounding=ROUND_CEILING));order=D(packs)*pack
        arrival,_=self._delivery_date(term,cutoff)
        if arrival is None:return D("Infinity"),True,term.unit_cost,term.lead_time_days,term.supplier_id
        adjusted=self._apply_shelf_life_policy(ingredient_id,base_unit,demands,lots,existing_inbound,baseline,
            term,arrival,order,packs,safety,minimum,shelf_life_days)
        risk=D(adjusted[2]["quantity_at_risk"])
        first=baseline.get("first_shortage_date")
        late=bool(first and arrival>date.fromisoformat(first))
        return risk,late,term.unit_cost,term.lead_time_days,term.supplier_id
    def _simulate_candidate(self,ingredient_id,base_unit,demands,lots,existing_inbound,arrival,order,order_unit):
        inbound=list(existing_inbound)
        inbound_quantity=convert_quantity(order,order_unit,base_unit)
        if inbound_quantity:
            inbound.append({"date":arrival,"quantity":inbound_quantity,"lot_id":f"shelf-life-candidate:{ingredient_id}"})
        return self.simulator.simulate(ingredient_id,base_unit,demands,lots,inbound)
    @staticmethod
    def _projected_inventory_at_arrival(baseline,arrival):
        daily=baseline["daily"]
        on_or_after=next((row for row in daily if date.fromisoformat(row["date"])>=arrival),None)
        if on_or_after is not None:
            return max(D(0),D(on_or_after["opening_inventory"])+D(on_or_after["inbound_quantity"])-D(on_or_after["expired_quantity"]))
        return D(baseline["ending_inventory"])
    @staticmethod
    def _simulation_value_through(simulation,field,through,aggregate):
        values=[D(row[field]) for row in simulation["daily"] if date.fromisoformat(row["date"])<=through]
        return aggregate(values,D(0)) if aggregate is sum else aggregate(values)
    @staticmethod
    def _empty_line(ingredient,day,unit,raw,reasons,warnings):return {"ingredient_id":ingredient,"supplier_id":None,"order_date":day,"expected_arrival_date":None,
        "supplier_term_id":None,
        "raw_required_quantity":raw,"order_quantity":D(0),"unit":unit,"pack_count":None,"unit_cost":None,"line_cost":0,"moq":None,"pack_size":None,
        "lead_time_days":None,"rounding_excess":D(0),"reason_codes":reasons,"warnings":warnings}
