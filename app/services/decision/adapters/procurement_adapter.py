"""Build a pure core procurement request from canonical backend state."""

from __future__ import annotations

from collections import defaultdict
import json
from datetime import timedelta
import pandas as pd
from sqlalchemy import select

from app.services.budget_resolver import BudgetResolver
from app.services.business_constraint_resolver import BusinessConstraintResolver
from app.services.shortage_economics import build_shortage_economics
from app.services.procurement_planning_service import ProcurementPlanningService
from app.models.operations import ForecastResidualModel
from app.models.business import ProductModel
from app.services.decision.adapters.bom_adapter import CoreBomAdapter
from shelfcash_core.inventory.contracts import (
    InboundDelivery, InventoryDemandLine, InventoryDemandScenario, InventoryLot,
    InventorySimulationPolicy, ConsequenceCostAssumption,
)
from shelfcash_core.inventory.stress import StressScenarioDefinition
from shelfcash_core.inventory.simulator import simulate_inventory_scenarios
from shelfcash_core.optimization.contracts import OptimizationRequest, SupplierOffer
from shelfcash_core.optimization.expiry import resolve_inbound_expiry
from shelfcash_core.optimization.optimizer import optimize_procurement
from shelfcash_core.scenario.composer import generate_product_demand_scenarios


class CoreProcurementAdapter:
    """The only place where ORM state is translated for the core optimizer.

    The optimizer receives the documented P25/P50/P75 *design* set without
    weights.  Persisted walk-forward OOS residuals are used only for optional
    fixed-plan risk evaluation, never as stochastic optimizer input.
    """

    def __init__(self, session):
        self.session = session
        self.legacy_state = ProcurementPlanningService(session)
        self.constraints = BusinessConstraintResolver(session)

    def optimize(self, store_id, forecast, demand_rows, budget_override=None, use_open_purchase_orders=True, *,
                 predictions=None, engine_mode="deterministic", scenario_count=100, seed=42, scenario_method="residual_bootstrap", supplier_delay_days=0):
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
                    unit=unit, arrival_date=row["date"] + timedelta(days=supplier_delay_days), expiry_date=resolve_inbound_expiry(
                        arrival_date=row["date"] + timedelta(days=supplier_delay_days), shelf_life_days=row.get("shelf_life_days")
                    ),
                    provenance={"expiry_source": "purchase_order_line.shelf_life_days" if row.get("shelf_life_days") is not None else "not_configured"},
                ))
            for term in self.legacy_state._terms(store_id, ingredient_id):
                offers.append(SupplierOffer(
                    offer_id=term.constraint_id, supplier_id=term.supplier_id,
                    store_id=store_id, ingredient_id=ingredient_id, unit=term.unit,
                    order_date=decision_date, pack_size=float(term.pack_size),
                    unit_price=float(term.unit_cost), minimum_order_quantity=float(term.moq),
                    lead_time_days=term.lead_time_days + supplier_delay_days, available=True,
                    shelf_life_days=term.shelf_life_days,
                    available_delivery_days=(
                        None if term.available_delivery_days is None
                        else json.loads(term.available_delivery_days)
                    ),
                ))
        # The optimizer always receives the established unweighted quantile
        # design set.  A probability-weighted residual bootstrap, when
        # available, is retained separately for fixed-plan risk evaluation.
        scenarios = []
        risk_scenarios = []
        scenario_metadata = {
            "method": "quantile_design_fallback", "stochastic_enabled": False,
            "risk_status": "not_evaluated", "risk_reason": "MONTE_CARLO_DISABLED",
            "warnings": [],
        }
        if engine_mode == "stochastic" and predictions and forecast.model_version:
            # Do not mix calibration residuals across forecast model versions.
            residuals = list(self.session.scalars(select(ForecastResidualModel).where(
                ForecastResidualModel.store_id == store_id,
                ForecastResidualModel.model_version == forecast.model_version,
            )))
            if residuals:
                frame = pd.DataFrame([{"forecast_origin": r.forecast_origin, "target_date": r.target_date, "horizon": r.horizon,
                    "store_id": r.store_id, "product_id": r.product_id, "actual": float(r.actual_value), "p25": float(r.predicted_p25),
                    "p50": float(r.predicted_p50), "p75": float(r.predicted_p75), "raw_residual": float(r.residual),
                    "scaled_residual": float(r.residual) / max(float(r.predicted_p75-r.predicted_p25), 1e-6),
                    "target_train_eligible": True, "residual_source": "walk_forward_oos"} for r in residuals])
                try:
                    bundle = generate_product_demand_scenarios(CoreBomAdapter(self.session).forecast_package(store_id, forecast, predictions), frame, n_scenarios=scenario_count, seed=seed, method=scenario_method)
                    ingredient_bundle = CoreBomAdapter(self.session).expand_scenarios(store_id, forecast, predictions, bundle)
                    risk_scenarios = [InventoryDemandScenario(scenario_id=x.scenario_id, probability_weight=x.probability_weight,
                        simulation_start_date=decision_date, simulation_end_date=horizon_end,
                        lines=[InventoryDemandLine(scenario_id=x.scenario_id, store_id=store_id, ingredient_id=line.ingredient_id, target_date=line.target_date, quantity=line.quantity, unit=line.unit) for line in x.lines],
                        provenance={"scenario_kind": bundle.scenario_method, **bundle.diagnostics}, warnings=x.warnings) for x in ingredient_bundle.scenarios]
                    scenario_metadata = {
                        "method": bundle.scenario_method, "stochastic_enabled": False,
                        "risk_status": "evaluated", "risk_reason": None,
                        "sample_count": len(risk_scenarios), "warnings": bundle.warnings,
                        "diagnostics": bundle.diagnostics,
                    }
                except Exception as exc:
                    # Residual history is a statistical prerequisite, never a
                    # license to manufacture probabilities from quantiles.
                    scenario_metadata = {
                        "method": "quantile_design_fallback", "stochastic_enabled": False,
                        "risk_status": "not_evaluated", "risk_reason": "RESIDUAL_DISTRIBUTION_NOT_AVAILABLE",
                        "warnings": ["RISK_METRIC_NOT_AVAILABLE"],
                        "diagnostics": {"error_type": type(exc).__name__},
                    }
            else:
                scenario_metadata.update({
                    "risk_reason": "RESIDUAL_DISTRIBUTION_NOT_AVAILABLE",
                    "warnings": ["RISK_METRIC_NOT_AVAILABLE"],
                })
        elif engine_mode == "stochastic" and predictions:
            scenario_metadata.update({
                "risk_reason": "RESIDUAL_DISTRIBUTION_NOT_AVAILABLE",
                "warnings": ["RISK_METRIC_NOT_AVAILABLE"],
            })
        # Quantiles remain deterministic design/stress inputs, not probability
        # scenarios.  They must be present even when risk simulation succeeds.
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
        optimizer_scenarios = (
            risk_scenarios
            if engine_mode == "stochastic" and risk_scenarios
            else scenarios
        )
        budget = BudgetResolver(self.session).resolve(store_id, decision_date, budget_override, horizon_end).limit
        # BOM contributions already include canonical recipe/yield/loss and
        # combo expansion.  Derive one base shortage assumption per decision.
        product_prices = {row.product_id: row.price for row in self.session.scalars(
            select(ProductModel).where(ProductModel.store_id == store_id)
        )}
        reference_costs = {}
        for ingredient_id, rows in rows_by_ingredient.items():
            unit = rows[0].unit
            valid = [offer.unit_price for offer in offers if offer.ingredient_id == ingredient_id and offer.unit == unit]
            if valid:
                reference_costs[ingredient_id] = min(valid)
        economics = build_shortage_economics(
            demand_rows=demand_rows, product_prices=product_prices, reference_costs=reference_costs,
        )
        # Ingredient maximum_stock is the only storage constraint that can be
        # expressed safely without a cross-ingredient volume model.  Resolve
        # it once here and carry the same canonical limit into both the MILP
        # and Exact FEFO via OptimizationRequest.cost_assumptions.
        capacity_assumptions = []
        for ingredient_id, rows in rows_by_ingredient.items():
            unit = rows[0].unit
            limit = self.constraints.resolve_quantity(
                store_id, "maximum_stock", ingredient_id, unit, decision_date
            )
            economic = economics.get(ingredient_id, {})
            shortage = economic.get("shortage_cost_per_unit")
            if limit is not None or shortage is not None:
                capacity_assumptions.append(ConsequenceCostAssumption(
                    store_id=store_id, ingredient_id=ingredient_id, unit=unit,
                    shortage_cost_per_unit=0 if shortage is None else shortage,
                    capacity_quantity=float(limit),
                ))
        storage_constraint = self.constraints.resolve_storage_capacity(store_id, decision_date)
        capacity_context = {
            "ingredient_maximum_stock": {
                "status": "evaluated" if capacity_assumptions else "not_configured",
                "constraint_count": len(capacity_assumptions),
                "checkpoint": "post_receipt_pre_expiry_pre_consumption",
            },
            "store_storage": (
                {"status": "not_configured"}
                if storage_constraint is None
                else {
                    "status": "not_evaluated",
                    "constraint_id": storage_constraint.constraint_id,
                    "constraint_type": storage_constraint.constraint_type,
                    "capacity_value": float(storage_constraint.value),
                    "capacity_unit": storage_constraint.unit,
                    "reason": "INGREDIENT_STORAGE_VOLUME_METADATA_NOT_CONFIGURED",
                }
            ),
        }
        request = OptimizationRequest(
            request_id=f"forecast-{forecast.forecast_run_id}", decision_date=decision_date,
            planning_end_date=horizon_end, initial_inventory=lots, demand_scenarios=optimizer_scenarios,
            supplier_offers=offers, existing_inbound=inbound, budget=budget,
            cost_assumptions=capacity_assumptions,
            capacity_context=capacity_context,
            inventory_policy=InventorySimulationPolicy(unknown_expiry="warn_and_place_last", trace_retention="summary"),
            stress_scenarios=[
                StressScenarioDefinition(stress_id="demand_plus_20", demand_multiplier=1.2),
                StressScenarioDefinition(stress_id="demand_plus_30", demand_multiplier=1.3),
                StressScenarioDefinition(stress_id="supplier_delay_1d", supplier_delay_days=1),
                StressScenarioDefinition(stress_id="supplier_delay_2d", supplier_delay_days=2),
                StressScenarioDefinition(stress_id="demand_plus_20_delay_2d", demand_multiplier=1.2, supplier_delay_days=2),
            ],
            # In SAA mode the weighted set is already the optimizer input and
            # normal post-solve Exact FEFO uses it directly.  Deterministic
            # mode keeps this separate as an evaluation-only set.
            risk_demand_scenarios=([] if optimizer_scenarios is risk_scenarios else risk_scenarios),
            risk_evaluation_metadata={
                "status": scenario_metadata["risk_status"],
                "reason": scenario_metadata["risk_reason"],
                "method": scenario_metadata["method"] if risk_scenarios else None,
                "sample_count": len(risk_scenarios),
                "seed": seed,
            },
            stochastic=(optimizer_scenarios is risk_scenarios), seed=seed,
        )
        self.scenario_metadata = scenario_metadata
        self.shortage_economics = economics
        # Baseline is deliberately exact FEFO and happens before optimization.
        baseline = simulate_inventory_scenarios(
            request.initial_inventory, request.demand_scenarios, request.existing_inbound,
            policy=request.inventory_policy, simulation_start_date=decision_date,
            simulation_end_date=horizon_end,
        )
        return optimize_procurement(request), request, baseline, scenario_metadata
