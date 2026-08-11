"""Business-facing projection metrics derived from already-computed Exact FEFO output."""

from __future__ import annotations


def build_business_metrics(*, purchase_cost, simulation, recommended: bool,
                           risk_simulation=None, risk_metadata=None):
    if not recommended or simulation is None:
        return {"projected_purchase_cost": None, "deterministic": None,
                "probabilistic": {"status": "not_evaluated", "reason": "no_recommended_candidate",
                                  "stockout_probability": None, "expected_fill_rate": None,
                                  "expected_shortage": None, "expected_waste_quantity": None}}
    grouped = {}
    for result in simulation.results:
        for item in result.summary.by_key:
            key = (item.ingredient_id, item.unit)
            current = grouped.get(key)
            row = {"ingredient_id": item.ingredient_id, "unit": item.unit,
                   "fill_rate": item.fill_rate, "demand_quantity": item.total_demand,
                   "fulfilled_quantity": item.fulfilled_quantity, "shortage_quantity": item.shortage_quantity,
                   "expired_quantity": item.expired_quantity, "waste_quantity": item.explicit_waste_quantity,
                   "ending_quantity": item.ending_inventory, "days_of_supply": item.days_of_supply,
                   "first_stockout_date": item.projected_stockout_date,
                   "stockout_event_count": item.stockout_event_count}
            if current is None:
                grouped[key] = row
            else:  # deterministic design scenarios: preserve conservative per-unit values, never sum scenarios.
                current["fill_rate"] = min(current["fill_rate"], row["fill_rate"])
                for field in ("shortage_quantity", "expired_quantity", "waste_quantity", "stockout_event_count"):
                    current[field] = max(current[field], row[field])
                dates = [x for x in (current["first_stockout_date"], row["first_stockout_date"]) if x is not None]
                current["first_stockout_date"] = min(dates) if dates else None
                values = [x for x in (current["days_of_supply"], row["days_of_supply"]) if x is not None]
                current["days_of_supply"] = min(values) if values else None
    ingredients = sorted(grouped.values(), key=lambda x: (x["fill_rate"], x["ingredient_id"]))
    stockouts = [x for x in ingredients if x["stockout_event_count"] or x["shortage_quantity"] > 0]
    expiry = [x for x in ingredients if x["expired_quantity"] > 0]
    waste = [x for x in ingredients if x["waste_quantity"] > 0]
    weighted = risk_simulation.risk_metrics if risk_simulation is not None else None
    risk_metadata = risk_metadata or {}
    capacity_rows = []
    for result in simulation.results:
        capacity_keys = {
            (entry["store_id"], entry["ingredient_id"])
            for entry in result.provenance.get("capacity_evaluated_keys", [])
        }
        for item in result.daily_ledgers:
            if (item.store_id, item.ingredient_id) not in capacity_keys:
                continue
            limit = item.maximum_quantity - item.capacity_violation_quantity
            key = (item.ingredient_id, item.unit)
            previous = next((row for row in capacity_rows if (row["ingredient_id"], row["unit"]) == key), None)
            row = {"constraint": "maximum_stock", "ingredient_id": item.ingredient_id,
                   "unit": item.unit, "limit": limit, "peak_projected_inventory": item.maximum_quantity,
                   "utilization": (item.maximum_quantity / limit if limit > 0 else None),
                   "peak_date": item.simulation_date.isoformat(),
                   "excess": item.capacity_violation_quantity,
                   "metric_source": "exact_fefo"}
            if previous is None:
                capacity_rows.append(row)
            elif row["peak_projected_inventory"] > previous["peak_projected_inventory"]:
                capacity_rows[capacity_rows.index(previous)] = row
    capacity_context = next((r.provenance.get("capacity_context") for r in simulation.results
                             if r.provenance.get("capacity_context")), {})
    store_capacity = capacity_context.get("store_storage", {})
    capacity_status = (
        "violation" if any(row["excess"] > 0 for row in capacity_rows)
        else "partially_evaluated" if capacity_rows and store_capacity.get("status") == "not_evaluated"
        else "pass" if capacity_rows
        else store_capacity.get("status", "not_configured")
    )
    return {"projected_purchase_cost": purchase_cost,
            "deterministic": {"metric_source": "exact_fefo", "minimum_fill_rate": min((x["fill_rate"] for x in ingredients), default=None),
                                "ingredient_metrics": ingredients, "shortage_by_ingredient": [x for x in ingredients if x["shortage_quantity"] > 0],
                                "expiry_by_ingredient": expiry, "waste_by_ingredient": waste,
                                "ingredients_with_stockout": stockouts,
                                "first_stockout_date": min((x["first_stockout_date"] for x in stockouts if x["first_stockout_date"]), default=None),
                                "capacity": {"status": capacity_status, "checkpoint": "post_receipt_pre_expiry_pre_consumption",
                                             "constraints": capacity_rows,
                                             "store_storage": store_capacity or None}},
            "probabilistic": {
                "status": "evaluated" if weighted else risk_metadata.get("status", "not_evaluated"),
                "reason": None if weighted else risk_metadata.get("reason", "probability_weights_unavailable"),
                "method": risk_metadata.get("method"),
                "sample_count": weighted.scenario_count if weighted else risk_metadata.get("sample_count"),
                "metric_source": "stochastic_exact_fefo" if weighted else None,
                "stockout_probability": weighted.any_stockout_probability if weighted else None,
                "expected_fill_rate": weighted.mean_key_fill_rate if weighted else None,
                # Legacy scalars remain null for mixed units.  Unit-safe values
                # are represented per ingredient below.
                "expected_shortage": None, "expected_waste_quantity": None,
                "expected_shortage_by_ingredient": ([] if not weighted else [
                    {"ingredient_id": item.ingredient_id, "unit": item.unit,
                     "quantity": item.expected_shortage}
                    for item in weighted.by_key
                ]),
                "expected_waste_by_ingredient": ([] if not weighted else [
                    {"ingredient_id": item.ingredient_id, "unit": item.unit,
                     "quantity": item.expected_explicit_waste}
                    for item in weighted.by_key
                ]),
                "stockout_probability_by_ingredient": ([] if not weighted else [
                    {"ingredient_id": item.ingredient_id, "unit": item.unit,
                     "probability": item.stockout_probability}
                    for item in weighted.by_key
                ]),
            }}
