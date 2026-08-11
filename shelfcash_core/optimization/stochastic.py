"""First-stage-only, expiry-bucket Sample Average Approximation MILP."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from shelfcash_core.bom.units import normalize_unit
from shelfcash_core.exceptions import OptimizationNotAvailableError
from shelfcash_core.optimization.contracts import OptimizationRequest, ProcurementPlan, StrategyProfile
from shelfcash_core.optimization.deterministic import _Variables, _decision, _offer_upper_packs
from shelfcash_core.optimization.model_data import build_problem_data, shortage_cost_per_target_unit


def solve_stochastic_procurement(request: OptimizationRequest, profile: StrategyProfile) -> ProcurementPlan:
    """Solve weighted SAA with shared orders and scenario-specific FEFO buckets.

    The purchase-pack variables deliberately have no scenario index.  Only
    inventory, consumption, expiry loss and shortage vary by realized demand.
    """
    data = build_problem_data(request)
    if not data.probabilistic_weights or len(data.scenario_ids) < 2:
        raise OptimizationNotAvailableError("Stochastic optimization requires weighted scenarios.")
    if any(float(weight) <= 0 for weight in data.scenario_weights):
        raise OptimizationNotAvailableError("Zero-weight scenarios are not valid SAA inputs.", code="INVALID_SCENARIO_WEIGHTS")

    max_demand = {key: max(sum(data.demand.get((s, key, d), 0) for d in data.dates) for s in data.scenario_ids) for key in data.keys}
    variables = _Variables(); x = {}; y = {}; upper = {}
    for n, offer in enumerate(data.regular_offers):
        upper[n] = _offer_upper_packs(offer, request, max_demand)
        x[n] = variables.add(offer.offer.pack_size * offer.offer.unit_price * (1 + profile.cash_penalty), 0, upper[n], True)
        y[n] = variables.add(offer.offer.delivery_cost * (1 + profile.cash_penalty), 0, 1, True)

    buckets = {key: [None] for key in data.keys}; initial = {}
    for key in data.keys:
        initial[(key, None)] = data.initial_quantity.get(key, 0) - sum(v.quantity for v in data.initial_expiry_buckets if v.key == key)
    for source in (data.initial_expiry_buckets, data.existing_inbound_expiry_buckets):
        for item in source:
            if item.expiry_date not in buckets[item.key]: buckets[item.key].append(item.expiry_date)
            initial.setdefault((item.key, item.expiry_date), 0.0)
            if source is data.initial_expiry_buckets: initial[(item.key, item.expiry_date)] = item.quantity
    for offer in data.regular_offers:
        key = (offer.offer.store_id, offer.offer.ingredient_id)
        if offer.expiry_date is not None and offer.expiry_date not in buckets[key]:
            buckets[key].append(offer.expiry_date); initial[(key, offer.expiry_date)] = 0.0

    inv = {}; consume = {}; expired = {}; shortage = {}
    for s, weight in zip(data.scenario_ids, data.scenario_weights, strict=True):
        for key in data.keys:
            assumption = data.assumptions.get(key)
            hold = float(weight) * profile.holding_penalty * (assumption.holding_cost_per_unit_day if assumption else 0)
            short = float(weight) * profile.shortage_penalty * shortage_cost_per_target_unit(data, key)[0]
            for bucket in buckets[key]:
                for day in data.dates:
                    usable = bucket is None or (day <= bucket if request.inventory_policy.expiry_inclusive else day < bucket)
                    inv[(s,key,bucket,day)] = variables.add(hold, 0, np.inf if usable else 0)
                    consume[(s,key,bucket,day)] = variables.add(0, 0, np.inf if usable else 0)
                    expired[(s,key,bucket,day)] = variables.add(0, 0, np.inf if bucket is not None and not usable else 0)
            for day in data.dates:
                shortage[(s,key,day)] = variables.add(short, 0, data.demand.get((s,key,day),0))

    rows=[]; lows=[]; highs=[]
    def add(c, lo, hi): rows.append(c); lows.append(lo); highs.append(hi)
    for n, offer in enumerate(data.regular_offers):
        minimum = math.ceil(offer.offer.minimum_order_quantity / offer.offer.pack_size - 1e-12)
        add({x[n]:1,y[n]:-upper[n]}, -np.inf, 0); add({x[n]:-1,y[n]:minimum}, -np.inf, 0)

    for s in data.scenario_ids:
        for key in data.keys:
            for bucket in buckets[key]:
                previous=None
                for day in data.dates:
                    c={inv[(s,key,bucket,day)]:1, consume[(s,key,bucket,day)]:1, expired[(s,key,bucket,day)]:1}
                    starting=0.0
                    if previous is None: starting=initial[(key,bucket)]
                    else: c[previous]=-1
                    for n, offer in enumerate(data.regular_offers):
                        if (offer.offer.store_id,offer.offer.ingredient_id)==key and offer.arrival_date==day and offer.expiry_date==bucket:
                            c[x[n]]=-offer.pack_quantity_target
                    known=sum(v.quantity for v in data.existing_inbound_expiry_buckets if v.key==key and v.arrival_date==day)
                    if bucket is None: starting += data.existing_inbound.get((key,day),0)-known
                    else: starting += sum(v.quantity for v in data.existing_inbound_expiry_buckets if v.key==key and v.arrival_date==day and v.expiry_date==bucket)
                    add(c, starting, starting); previous=inv[(s,key,bucket,day)]
            # Capacity checkpoint: receipt, then before expiry/consumption.
            assumption=data.assumptions.get(key)
            if assumption and assumption.capacity_quantity is not None:
                for offset,day in enumerate(data.dates):
                    c={}; constant=data.existing_inbound.get((key,day),0)
                    if offset:
                        for bucket in buckets[key]: c[inv[(s,key,bucket,data.dates[offset-1])]]=1
                    else: constant += sum(initial[(key,bucket)] for bucket in buckets[key])
                    for n,offer in enumerate(data.regular_offers):
                        if (offer.offer.store_id,offer.offer.ingredient_id)==key and offer.arrival_date==day: c[x[n]]=offer.pack_quantity_target
                    add(c,-np.inf,assumption.capacity_quantity-constant)
            for day in data.dates:
                c={shortage[(s,key,day)]:1}
                for bucket in buckets[key]: c[consume[(s,key,bucket,day)]]=1
                demand=data.demand.get((s,key,day),0); add(c,demand,demand)

    # Existing protected expected-fill policy, now genuinely probability weighted.
    if profile.minimum_expected_fill_rate is not None:
        for key in data.keys:
            c={}
            for s,w in zip(data.scenario_ids,data.scenario_weights,strict=True):
                total=sum(data.demand.get((s,key,d),0) for d in data.dates)
                if total:
                    for d in data.dates: c[shortage[(s,key,d)]]=float(w)/total
            add(c,-np.inf,1-profile.minimum_expected_fill_rate)
    if request.budget is not None:
        c={}
        for n,offer in enumerate(data.regular_offers): c[x[n]]=offer.offer.pack_size*offer.offer.unit_price; c[y[n]]=offer.offer.delivery_cost
        add(c,-np.inf,request.budget)
    for constraint in request.supplier_constraints:
        matching=[(n,o) for n,o in enumerate(data.regular_offers) if o.offer.supplier_id==constraint.supplier_id and (constraint.store_id is None or o.offer.store_id==constraint.store_id) and (constraint.ingredient_id is None or o.offer.ingredient_id==constraint.ingredient_id)]
        if constraint.maximum_total_quantity is not None:
            if any(normalize_unit(o.offer.unit)!=normalize_unit(constraint.unit or "") for _,o in matching): raise OptimizationNotAvailableError("Supplier quantity cap unit does not match scoped offers.",code="INVALID_PROCUREMENT_UNIT")
            add({x[n]:o.offer.pack_size for n,o in matching},-np.inf,constraint.maximum_total_quantity)
        if constraint.maximum_total_cost is not None:
            c={}
            for n,o in matching: c[x[n]]=o.offer.pack_size*o.offer.unit_price; c[y[n]]=o.offer.delivery_cost
            add(c,-np.inf,constraint.maximum_total_cost)

    matrix=np.zeros((len(rows),len(variables.cost)))
    for r,c in enumerate(rows):
        for col,val in c.items(): matrix[r,col]=val
    result=milp(c=np.asarray(variables.cost),integrality=np.asarray(variables.integrality),bounds=Bounds(variables.lower,variables.upper),constraints=LinearConstraint(matrix,lows,highs),options={"time_limit":30})
    status={0:"OPTIMAL",1:"LIMIT_REACHED",2:"INFEASIBLE",3:"UNBOUNDED"}.get(result.status,"SOLVER_ERROR")
    orders=[]
    if result.x is not None and result.status in {0,1}:
        for n,offer in enumerate(data.regular_offers):
            packs=round(result.x[x[n]])
            if packs>0: orders.append(_decision(offer,packs))
    purchase_cost=sum(line.purchase_cost+line.delivery_cost for line in orders)
    purchase_term=sum((line.purchase_cost+line.delivery_cost)*(1+profile.cash_penalty) for line in orders)
    shortage_term=sum(float(result.x[i])*variables.cost[i] for i in shortage.values()) if result.x is not None else None
    holding_term=sum(float(result.x[i])*variables.cost[i] for i in inv.values()) if result.x is not None else None
    scenario_outcomes={}
    if result.x is not None:
        for s in data.scenario_ids:
            per_key={}
            for key in data.keys:
                total=sum(data.demand.get((s,key,d),0) for d in data.dates); missing=sum(float(result.x[shortage[(s,key,d)]]) for d in data.dates)
                per_key[f"{key[0]}|{key[1]}|{data.target_units[key]}"]={"demand":total,"shortage":missing,"fill_rate":1-missing/total if total else 1.0,"stockout":missing>1e-8}
            scenario_outcomes[s]={"stockout":any(v["stockout"] for v in per_key.values()),"per_key":per_key}
    return ProcurementPlan(plan_id=f"{request.request_id}-{profile.name.lower()}",strategy=profile.name,orders=orders,purchase_cost=purchase_cost,expected_recourse_cost=0,objective_value=float(result.fun) if result.fun is not None else None,solver_status=status,completed=False,provenance={"solver":"scipy.optimize.milp","formulation":"expiry_bucket_first_stage_saa_v1","first_stage_non_anticipative":True,"scenario_ids":data.scenario_ids,"scenario_weights":data.scenario_weights,"scenario_outcomes":scenario_outcomes,"exact_inventory_physics":False,"requires_m4_resimulation":True,"objective_breakdown":{"cost_unit":"supplier_offer_unit_price_currency_or_cost_unit","purchase_term":purchase_term,"shortage_term":shortage_term,"holding_term":holding_term,"waste_term":0.0,"total_objective":float(result.fun) if result.fun is not None else None}},warnings=list(data.warnings))
