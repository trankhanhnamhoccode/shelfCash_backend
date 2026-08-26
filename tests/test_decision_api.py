from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import json
from sqlalchemy import func, select

from app.models.business import (IngredientModel, InventoryLotModel, InventoryMovementModel,
    ProductModel, RecipeLineModel, RecipeVersionModel, SupplierIngredientTermModel, SupplierModel)
from app.models.operations import ForecastPredictionModel, ForecastResidualModel, ForecastRunModel
from app.services.decision.adapters.procurement_adapter import CoreProcurementAdapter
from app.models.decision import DecisionRunModel
from app.models.planning import IngredientDemandPredictionModel, ProcurementPlanModel


def test_core_decision_package_is_persisted_and_reloaded(client):
    sf = client.app.state.session_factory
    with sf() as s:
        product = ProductModel(product_id="decision-product", store_id="STORE_001", product="Tea", normalized_name="decision-tea", active=True, source="test")
        ingredient = IngredientModel(ingredient_id="decision-ingredient", store_id="STORE_001", ingredient="Tea leaf", normalized_name="decision-leaf", base_unit="kg", active=True, source="test")
        s.add_all([product, ingredient])
        s.add(ForecastRunModel(forecast_run_id="decision-forecast", store_id="STORE_001", cutoff_date=date(2026, 8, 3), horizon_days=1, quantiles_json="[0.25,0.5,0.75]", scope_json="{}", use_latest_calendar=True, status="completed", engine_status="test", request_hash="x", model_version="test", warnings_json="[]", created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc)))
        s.add(ForecastPredictionModel(prediction_id=str(uuid4()), forecast_run_id="decision-forecast", store_id="STORE_001", product_id=product.product_id, product_name="Tea", target_date=date(2026, 8, 4), horizon=1, p25=1, p50=2, p75=3, interval_lower=1, interval_upper=3, baseline_p50=2, calibration_source="test", warnings_json="[]", created_at=datetime.now(timezone.utc)))
        s.add(RecipeVersionModel(recipe_version_id="decision-recipe", store_id="STORE_001", product_id=product.product_id, version=1, effective_from=date(2026, 1, 1), content_hash="x", source="test", yield_quantity=Decimal("1"), process_loss_rate=Decimal("0")))
        s.add(RecipeLineModel(recipe_line_id="decision-line", recipe_version_id="decision-recipe", ingredient_id=ingredient.ingredient_id, quantity=Decimal("1"), unit="kg"))
        s.add(SupplierModel(supplier_id="decision-supplier", store_id="STORE_001", supplier="Supplier", normalized_name="decision-supplier", active=True, source="test"))
        s.add(SupplierIngredientTermModel(constraint_id="decision-term", store_id="STORE_001", supplier_id="decision-supplier", ingredient_id=ingredient.ingredient_id, unit_cost=100, moq=Decimal("1"), pack_size=Decimal("1"), lead_time_days=0, shelf_life_days=5, unit="kg", version=1, active=True, source="test"))
        s.add(InventoryLotModel(lot_id="decision-lot", store_id="STORE_001", ingredient_id=ingredient.ingredient_id, received_date=date(2026, 8, 3), initial_quantity=Decimal("0"), unit="kg", source="test", version=1))
        s.add(InventoryMovementModel(movement_id=str(uuid4()), store_id="STORE_001", lot_id="decision-lot", movement_type="opening_balance", quantity_delta=Decimal("0"), unit="kg", occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc), source="test"))
        s.commit()
    response = client.post("/api/v1/stores/STORE_001/decision-runs", json={"forecast_run_id":"decision-forecast", "as_of_date":"2026-08-03", "horizon_days":1, "engine_mode":"deterministic"})
    assert response.status_code == 200, response.text
    package = response.json()
    assert package["engine_mode"] == "deterministic"
    assert package["technical_metrics"]["baseline_engine"] == "lot_level_fefo_v1"
    assert package["technical_metrics"]["forecast_trace"] == {
        "decision_run_id": package["decision_run_id"],
        "requested_forecast_run_id": "decision-forecast",
        "resolved_forecast_run_id": "decision-forecast",
        "forecast_store_id": "STORE_001",
        "forecast_cutoff_date": "2026-08-03",
        "forecast_target_min": "2026-08-04",
        "forecast_target_max": "2026-08-04",
        "prediction_count": 1,
    }
    assert package["ingredient_demand"][0]["p50"] == 2.0
    assert package["ingredient_demand"][0]["target_date"] == "2026-08-04"
    # The forecast cutoff is an EOD snapshot.  Core ``decision_date`` and
    # exact FEFO must both begin on forecast day one, never on the cutoff.
    assert package["inventory_risk"]["simulation_start_date"] == "2026-08-04"
    assert package["inventory_risk"]["simulation_end_date"] == "2026-08-04"
    order = package["recommended_plan"]["items"][0]
    assert order["order_date"] == "2026-08-04"
    assert order["arrival_date"] == "2026-08-04"  # explicit zero lead time
    with sf() as s:
        s.get(RecipeLineModel, "decision-line").quantity = Decimal("2")
        s.commit()
    refreshed = client.post("/api/v1/stores/STORE_001/decision-runs", json={"forecast_run_id":"decision-forecast", "as_of_date":"2026-08-03", "horizon_days":1, "engine_mode":"deterministic"})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["ingredient_demand"][0]["p50"] == 4.0
    restored = client.get(f"/api/v1/decision-runs/{package['decision_run_id']}")
    assert restored.status_code == 200
    assert restored.json() == package
    brief = client.get(f"/api/v1/decision-runs/{package['decision_run_id']}/brief")
    assert brief.status_code == 200, brief.text
    assert brief.json()["recommendation"]["available"] is True
    assert brief.json()["procurement_rows"][0]["ingredient_id"] == "decision-ingredient"
    assert brief.json()["risk"]["stockout_probability"] is None
    assert brief.json()["ingredient_demand"][0]["target_date"] == package["ingredient_demand"][0]["target_date"]
    explanation = client.post(f"/api/v1/decision-runs/{package['decision_run_id']}/explanation", json={"language":"vi", "detail_level":"simple", "question":"Tại sao phải nhập decision-ingredient?"})
    assert explanation.status_code == 200
    assert explanation.json()["source"] == "template"
    assert explanation.json()["provider"] == "shelfcash_decision_intelligence"
    evidence_ids = {item["evidence_id"] for item in brief.json()["evidence"]}
    assert set(explanation.json()["citations"][0]["evidence_id"] for _ in [0]) <= evidence_ids
    with sf() as s:
        before_counts = {
            "decision": s.scalar(select(func.count()).select_from(DecisionRunModel)),
            "forecast": s.scalar(select(func.count()).select_from(ForecastRunModel)),
            "prediction": s.scalar(select(func.count()).select_from(ForecastPredictionModel)),
            "demand": s.scalar(select(func.count()).select_from(IngredientDemandPredictionModel)),
            "plan": s.scalar(select(func.count()).select_from(ProcurementPlanModel)),
        }
    what_if = client.post(f"/api/v1/decision-runs/{package['decision_run_id']}/what-if", json={"demand_multiplier":1.3, "supplier_delay_days":1, "budget_limit":10000000, "strategy":"protected"})
    assert what_if.status_code == 200
    assert what_if.json()["baseline"]["decision_run_id"] == package["decision_run_id"]
    assert what_if.json()["hypothetical"]["data_availability"]["authority"] == "HYPOTHETICAL"
    assert what_if.json()["baseline"]["ingredient_demand"][0]["target_date"] == package["ingredient_demand"][0]["target_date"]
    assert what_if.json()["hypothetical"]["ingredient_demand"][0]["target_date"] == package["ingredient_demand"][0]["target_date"]
    assert what_if.json()["comparison"]["stockout_probability_delta"] is None
    assert what_if.json()["mutations"] == {"demand_multiplier": 1.3, "supplier_delay_days": 1, "budget_limit": 10000000, "strategy": "protected"}
    assert what_if.json()["mutation_facts"]["demand_change_percent"] == 30.0
    assert what_if.json()["mutation_facts"]["demand_change_percentage_points"] == 30.0
    assert what_if.json()["comparison"]["new_issues"] == what_if.json()["comparison"]["new_risks"]
    assert what_if.json()["grounded_explanation"]["authority"] == "HYPOTHETICAL"
    with sf() as s:
        assert json.loads(s.get(DecisionRunModel, package["decision_run_id"]).package_json) == package
        assert before_counts == {
            "decision": s.scalar(select(func.count()).select_from(DecisionRunModel)),
            "forecast": s.scalar(select(func.count()).select_from(ForecastRunModel)),
            "prediction": s.scalar(select(func.count()).select_from(ForecastPredictionModel)),
            "demand": s.scalar(select(func.count()).select_from(IngredientDemandPredictionModel)),
            "plan": s.scalar(select(func.count()).select_from(ProcurementPlanModel)),
        }
    no_feasible = client.post(f"/api/v1/decision-runs/{package['decision_run_id']}/what-if", json={"budget_limit": 0})
    assert no_feasible.status_code == 200
    assert no_feasible.json()["hypothetical"]["recommendation"]["available"] is False
    assert no_feasible.json()["comparison"]["recommendation_changed"] is True


def test_stochastic_residual_selection_deduplicates_repeated_forecast_runs(client):
    sf = client.app.state.session_factory
    origin = date(2026, 8, 12)
    target = date(2026, 8, 13)
    with sf() as s:
        s.add_all([
            ForecastRunModel(
                forecast_run_id="residual-old", store_id="STORE_001", cutoff_date=origin,
                horizon_days=1, quantiles_json="[]", scope_json="{}", use_latest_calendar=True,
                status="completed", engine_status="test", request_hash="old", model_version="test-model",
                warnings_json="[]", created_at=datetime(2026, 8, 12, 8, tzinfo=timezone.utc),
                completed_at=datetime(2026, 8, 12, 8, tzinfo=timezone.utc),
            ),
            ForecastRunModel(
                forecast_run_id="residual-new", store_id="STORE_001", cutoff_date=origin,
                horizon_days=1, quantiles_json="[]", scope_json="{}", use_latest_calendar=True,
                status="completed", engine_status="test", request_hash="new", model_version="test-model",
                warnings_json="[]", created_at=datetime(2026, 8, 12, 9, tzinfo=timezone.utc),
                completed_at=datetime(2026, 8, 12, 9, tzinfo=timezone.utc),
            ),
        ])
        for run_id, residual_id, p50 in (
            ("residual-old", "residual-old-value", Decimal("2")),
            ("residual-new", "residual-new-value", Decimal("3")),
        ):
            s.add(ForecastResidualModel(
                residual_id=residual_id, store_id="STORE_001", forecast_run_id=run_id,
                product_id="residual-product", target_date=target, horizon=1,
                actual_value=Decimal("5"), predicted_p25=Decimal("1"), predicted_p50=p50,
                predicted_p75=Decimal("6"), residual=Decimal("2"), forecast_origin=origin,
                model_version="test-model", created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            ))
        s.commit()
        residuals = CoreProcurementAdapter._canonical_residuals("STORE_001", "test-model", s)

    assert [item.residual_id for item in residuals] == ["residual-new-value"]
