"""Decision-run shortage consequence assumptions from canonical business data."""

from __future__ import annotations

from collections import defaultdict
import json


def build_shortage_economics(*, demand_rows, product_prices, reference_costs):
    """Return complete-only contribution-margin bottleneck proxies.

    ``demand_rows`` are persisted BOM contributions, so yield, process loss,
    units and combo expansion have already been resolved by the canonical BOM
    engine.  A product with positive P50 demand and incomplete economics makes
    every ingredient it consumes fall back rather than silently understate it.
    """
    product_rows = defaultdict(list)
    ingredient_products = defaultdict(set)
    for row in demand_rows:
        contributions = getattr(row, "contributions", None)
        if contributions is None:
            contributions = json.loads(row.contributions_json or "[]")
        for source in contributions:
            demand = float(source["forecast_p50"])
            quantity = float(source["contribution_p50"])
            key = (source["product_id"], str(row.target_date))
            product_rows[key].append((row.ingredient_id, quantity, demand))
            if demand > 0:
                ingredient_products[row.ingredient_id].add(key)

    products = {}
    for key, components in product_rows.items():
        product_id = key[0]
        demand = components[0][2]
        price = product_prices.get(product_id)
        missing = []
        if price is None:
            missing.append("PRODUCT_PRICE_NOT_AVAILABLE")
        recipe_cost = 0.0
        for ingredient_id, quantity, _ in components:
            cost = reference_costs.get(ingredient_id)
            if cost is None:
                missing.append("SUPPLIER_REFERENCE_COST_NOT_AVAILABLE")
            else:
                recipe_cost += quantity / demand * cost if demand else 0.0
        margin = None if missing else max(float(price) - recipe_cost, 0.0)
        products[key] = {"demand": demand, "margin": margin, "missing": sorted(set(missing)),
                         "recipe_cost": recipe_cost if not missing else None}

    output = {}
    for ingredient_id, keys in ingredient_products.items():
        numerator = denominator = 0.0
        incomplete = []
        for key in keys:
            product = products[key]
            component = sum(quantity for item, quantity, _ in product_rows[key] if item == ingredient_id)
            if product["missing"]:
                incomplete.extend(product["missing"])
                continue
            numerator += product["demand"] * product["margin"]
            denominator += component
        if incomplete or denominator <= 0:
            output[ingredient_id] = {"source": "supplier_replacement_fallback",
                                     "reason": sorted(set(incomplete)) or ["ZERO_DEMAND_OR_USAGE"]}
        else:
            output[ingredient_id] = {"source": "derived_contribution_margin",
                                     "shortage_cost_per_unit": numerator / denominator,
                                     "supporting_products": len(keys),
                                     "forecast_ingredient_quantity": denominator,
                                     "contribution_margin_basis": numerator,
                                     "diagnostics": ["NON_POSITIVE_CONTRIBUTION_MARGIN"] if any(
                                         products[key]["margin"] == 0 for key in keys) else []}
    return output
