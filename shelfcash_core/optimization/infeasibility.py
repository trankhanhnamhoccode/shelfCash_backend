"""Bounded static and counterfactual diagnostics for infeasible candidates."""

from shelfcash_core.optimization.model_data import expected_daily_demand

MAX_INFEASIBILITY_PROBES = 3


def diagnose_infeasibility(*, data, request, probe):
    diagnostics = []
    daily = expected_daily_demand(data)
    for key in data.keys:
        unit = data.target_units[key]
        initial_unbounded = data.initial_quantity.get(key, 0.0) - sum(
            item.quantity for item in data.initial_expiry_buckets if item.key == key
        )
        first_offer = min((item.arrival_date for item in data.regular_offers
                           if (item.offer.store_id, item.offer.ingredient_id) == key), default=None)
        cumulative_demand = 0.0
        for day in data.dates:
            cumulative_demand += daily[(key, day)]
            finite_initial = sum(item.quantity for item in data.initial_expiry_buckets
                                 if item.key == key and (day <= item.expiry_date if request.inventory_policy.expiry_inclusive else day < item.expiry_date))
            known_inbound = sum(item.quantity for item in data.existing_inbound_expiry_buckets
                                if item.key == key and item.arrival_date <= day
                                and (day <= item.expiry_date if request.inventory_policy.expiry_inclusive else day < item.expiry_date))
            total_inbound = sum(quantity for ((inbound_key, inbound_day), quantity) in data.existing_inbound.items()
                                if inbound_key == key and inbound_day <= day)
            known_inbound_total = sum(item.quantity for item in data.existing_inbound_expiry_buckets
                                      if item.key == key and item.arrival_date <= day)
            available = initial_unbounded + finite_initial + known_inbound + total_inbound - known_inbound_total
            if cumulative_demand > available + 1e-9 and (first_offer is None or day < first_offer):
                diagnostics.append({"code": "NO_FEASIBLE_SUPPLY_SOURCE" if first_offer is None else "DEMAND_BEFORE_FIRST_FEASIBLE_ARRIVAL",
                    "constraint_family": "supply_chronology", "severity": "blocking", "confidence": "proven",
                    "ingredient_id": key[1], "date": day.isoformat(), "cumulative_demand": cumulative_demand,
                    "cumulative_usable_supply": available, "uncovered_quantity": cumulative_demand - available,
                    "unit": unit, "first_feasible_supplier_arrival": None if first_offer is None else first_offer.isoformat()})
                break
    probes = []
    for family in ("budget", "service", "capacity"):
        status = probe(family)
        probes.append({"family": family, "solver_status": status})
        if status == "OPTIMAL":
            diagnostics.append({"code": {"budget": "BUDGET_CONSTRAINT_CONTRIBUTES_TO_INFEASIBILITY",
                                           "service": "SERVICE_TARGET_CONTRIBUTES_TO_INFEASIBILITY",
                                           "capacity": "MAXIMUM_STOCK_CONTRIBUTES_TO_INFEASIBILITY"}[family],
                                "constraint_family": family, "severity": "blocking", "confidence": "counterfactual"})
    if not diagnostics:
        diagnostics.append({"code": "INFEASIBILITY_CAUSE_NOT_ISOLATED", "constraint_family": "unknown",
                            "severity": "blocking", "confidence": "not_evaluated", "probes": probes})
    elif sum(item["solver_status"] == "OPTIMAL" for item in probes) > 1:
        diagnostics.append({"code": "MULTIPLE_OR_INTERACTING_CONSTRAINTS", "constraint_family": "multiple",
                            "severity": "blocking", "confidence": "counterfactual", "probes": probes})
    return diagnostics, probes
