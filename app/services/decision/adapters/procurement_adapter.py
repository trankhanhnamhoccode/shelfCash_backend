"""Build a pure core procurement request from canonical backend state."""

from __future__ import annotations

from collections import defaultdict

from app.services.budget_resolver import BudgetResolver
from app.services.procurement_planning_service import ProcurementPlanningService
from shelfcash_core.inventory.contracts import (
    InboundDelivery, InventoryDemandLine, InventoryDemandScenario, InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.inventory.stress import StressScenarioDefinition
from shelfcash_core.optimization.contracts import OptimizationRequest, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement


class CoreProcurementAdapter:
    """The only place where ORM state is translated for the core optimizer.

    Forecast residuals are not persisted in the current canonical schema.  We
    therefore create the documented P25/P50/P75 *design* set without weights,
    explicitly disabling stochastic SAA rather than inventing probabilities.
    """

    def __init__(self, session):
        self.session = session
        self.legacy_state = ProcurementPlanningService(session)

    def optimize(self, store_id, forecast, demand_rows, budget_override=None, use_open_purchase_orders=True):
        rows_by_ingredient = defaultdict(list)
        for row in demand_rows:
            rows_by_ingredient[row.ingredient_id].append(row)
        decision_date = forecast.cutoff_date
        horizon_end = max(row.target_date for row in demand_rows)
        lots = []
        offers = []
        inbound = []
        open_inbound = self.legacy_state._open_inbound(store_id, decision_date) if use_open_purchase_orders else {}
        for ingredient_id, rows in rows_by_ingredient.items():
            unit = rows[0].unit
            for row in self.legacy_state._lots(store_id, ingredient_id, decision_date):
                lots.append(InventoryLot(
                    lot_id=row["lot_id"], store_id=store_id, ingredient_id=ingredient_id,
                    quantity_remaining=float(row["quantity"]), unit=unit,
                    received_date=row["received_date"], expiry_date=row.get("expiry_date"),
                    source_type="initial_inventory",
                ))
            for index, row in enumerate(open_inbound.get(ingredient_id, [])):
                inbound.append(InboundDelivery(
                    delivery_id=f"open-{ingredient_id}-{index}", lot_id=row["lot_id"],
                    purchase_order_id=row["lot_id"], supplier_id="open_purchase_order",
                    store_id=store_id, ingredient_id=ingredient_id, quantity=float(row["quantity"]),
                    unit=unit, arrival_date=row["date"],
                ))
            for term in self.legacy_state._terms(store_id, ingredient_id):
                offers.append(SupplierOffer(
                    offer_id=term.constraint_id, supplier_id=term.supplier_id,
                    store_id=store_id, ingredient_id=ingredient_id, unit=term.unit,
                    order_date=decision_date, pack_size=float(term.pack_size),
                    unit_price=float(term.unit_cost), minimum_order_quantity=float(term.moq),
                    lead_time_days=term.lead_time_days, available=True,
                ))
        scenarios = []
        for scenario_id, quantile in (("p25_design", "p25"), ("p50_design", "p50"), ("p75_design", "p75")):
            scenarios.append(InventoryDemandScenario(
                scenario_id=scenario_id, probability_weight=None,
                simulation_start_date=decision_date, simulation_end_date=horizon_end,
                lines=[InventoryDemandLine(
                    scenario_id=scenario_id, store_id=store_id,
                    ingredient_id=row.ingredient_id, target_date=row.target_date,
                    quantity=float(getattr(row, quantile)), unit=row.unit,
                ) for row in demand_rows],
                provenance={"scenario_kind": "quantile_design_fallback"},
                warnings=["SCENARIO_HISTORY_INSUFFICIENT"],
            ))
        budget = BudgetResolver(self.session).resolve(store_id, decision_date, budget_override, horizon_end).limit
        request = OptimizationRequest(
            request_id=f"forecast-{forecast.forecast_run_id}", decision_date=decision_date,
            planning_end_date=horizon_end, initial_inventory=lots, demand_scenarios=scenarios,
            supplier_offers=offers, existing_inbound=inbound, budget=budget,
            inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last", trace_retention="summary"),
            stress_scenarios=[
                StressScenarioDefinition(stress_id="demand_plus_20", demand_multiplier=1.2),
                StressScenarioDefinition(stress_id="demand_plus_30", demand_multiplier=1.3),
                StressScenarioDefinition(stress_id="supplier_delay_1d", supplier_delay_days=1),
                StressScenarioDefinition(stress_id="supplier_delay_2d", supplier_delay_days=2),
                StressScenarioDefinition(stress_id="demand_plus_20_delay_2d", demand_multiplier=1.2, supplier_delay_days=2),
            ],
            stochastic=False, seed=42,
        )
        return optimize_procurement(request), request
