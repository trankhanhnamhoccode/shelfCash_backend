"""Build a pure core procurement request from canonical backend state."""

from __future__ import annotations

from collections import defaultdict
import json
import pandas as pd
from sqlalchemy import select

from app.services.budget_resolver import BudgetResolver
from app.services.procurement_planning_service import ProcurementPlanningService
from app.models.operations import ForecastResidualModel
from app.services.decision.adapters.bom_adapter import CoreBomAdapter
from shelfcash_core.inventory.contracts import (
    InboundDelivery, InventoryDemandLine, InventoryDemandScenario, InventoryLot,
    InventorySimulationPolicy,
)
from shelfcash_core.inventory.stress import StressScenarioDefinition
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.optimization.contracts import OptimizationRequest, SupplierOffer
from shelfcash_core.optimization.optimizer import optimize_procurement
from shelfcash_core.scenario.composer import generate_product_demand_scenarios


class CoreProcurementAdapter:
    """The only place where ORM state is translated for the core optimizer.

    Forecast residuals are not persisted in the current canonical schema.  We
    therefore create the documented P25/P50/P75 *design* set without weights,
    explicitly disabling stochastic SAA rather than inventing probabilities.
    """

    def __init__(self, session):
        self.session = session
        self.legacy_state = ProcurementPlanningService(session)

    def optimize(self, store_id, forecast, demand_rows, budget_override=None, use_open_purchase_orders=True, *,
                 predictions=None, engine_mode="deterministic", scenario_count=100, seed=42, scenario_method="residual_bootstrap"):
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
                    available_delivery_days=(
                        None if term.available_delivery_days is None
                        else json.loads(term.available_delivery_days)
                    ),
                ))
        scenarios = []
        scenario_metadata = {"method": "quantile_design_fallback", "stochastic_enabled": False, "warnings": ["SCENARIO_HISTORY_INSUFFICIENT"]}
        if engine_mode == "stochastic" and predictions:
            residuals = list(self.session.scalars(select(ForecastResidualModel).where(ForecastResidualModel.store_id == store_id)))
            if residuals:
                frame = pd.DataFrame([{"forecast_origin": r.forecast_origin, "target_date": r.target_date, "horizon": r.horizon,
                    "store_id": r.store_id, "product_id": r.product_id, "actual": float(r.actual_value), "p25": float(r.predicted_p25),
                    "p50": float(r.predicted_p50), "p75": float(r.predicted_p75), "raw_residual": float(r.residual),
                    "scaled_residual": float(r.residual) / max(float(r.predicted_p75-r.predicted_p25), 1e-6),
                    "target_train_eligible": True, "residual_source": "walk_forward_oos"} for r in residuals])
                try:
                    bundle = generate_product_demand_scenarios(CoreBomAdapter(self.session).forecast_package(store_id, forecast, predictions), frame, n_scenarios=scenario_count, seed=seed, method=scenario_method)
                    ingredient_bundle = CoreBomAdapter(self.session).expand_scenarios(store_id, forecast, predictions, bundle)
                    scenarios = [InventoryDemandScenario(scenario_id=x.scenario_id, probability_weight=x.probability_weight,
                        simulation_start_date=decision_date, simulation_end_date=horizon_end,
                        lines=[InventoryDemandLine(scenario_id=x.scenario_id, store_id=store_id, ingredient_id=line.ingredient_id, target_date=line.target_date, quantity=line.quantity, unit=line.unit) for line in x.lines],
                        provenance={"scenario_kind": bundle.scenario_method, **bundle.diagnostics}, warnings=x.warnings) for x in ingredient_bundle.scenarios]
                    scenario_metadata = {"method": bundle.scenario_method, "stochastic_enabled": True, "warnings": bundle.warnings, "diagnostics": bundle.diagnostics}
                except Exception:
                    # Data insufficiency is an expected business fallback; preserve only the explicit warning.
                    scenario_metadata = {"method": "quantile_design_fallback", "stochastic_enabled": False, "warnings": ["SCENARIO_HISTORY_INSUFFICIENT"]}
        if not scenarios:
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
            stochastic=scenario_metadata["stochastic_enabled"], seed=seed,
        )
        self.scenario_metadata = scenario_metadata
        # Baseline is deliberately exact FEFO and happens before optimization.
        baseline = simulate_inventory_scenarios(
            request.initial_inventory, request.demand_scenarios, request.existing_inbound,
            policy=request.inventory_policy, simulation_start_date=decision_date,
            simulation_end_date=horizon_end,
        )
        return optimize_procurement(request), request, baseline, scenario_metadata
