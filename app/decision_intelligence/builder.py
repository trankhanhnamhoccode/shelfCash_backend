from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.models.business import IngredientModel, SupplierModel
from app.models.decision import DecisionRunModel
from app.models.operations import ForecastRunModel


def derive_verified_order_reasons(item: dict, package_reasons: list[dict]) -> list[str]:
    """Return only persisted/order-level reason codes that name this ingredient."""
    ingredient_id = item.get("ingredient_id")
    codes = {str(code) for code in item.get("reason_codes", []) if code}
    for reason in package_reasons:
        if reason.get("entity_id") == ingredient_id and reason.get("code"):
            codes.add(str(reason["code"]))
    return sorted(codes)


class DecisionBriefBuilder:
    def build(self, session, run: DecisionRunModel) -> DecisionBriefFacts:
        package = json.loads(run.package_json)
        ingredient_ids = {str(item.get("ingredient_id")) for item in package.get("ingredient_demand", [])}
        ingredient_ids.update(str(item.get("ingredient_id")) for item in package.get("recommended_plan", {}).get("items", []))
        supplier_ids = {str(item.get("supplier_id")) for item in package.get("recommended_plan", {}).get("items", []) if item.get("supplier_id")}
        ingredients = {row.ingredient_id: row.ingredient for row in session.scalars(select(IngredientModel).where(IngredientModel.ingredient_id.in_(ingredient_ids)))} if ingredient_ids else {}
        suppliers = {row.supplier_id: row.supplier for row in session.scalars(select(SupplierModel).where(SupplierModel.supplier_id.in_(supplier_ids)))} if supplier_ids else {}
        forecast = session.get(ForecastRunModel, run.forecast_run_id)
        selected = package.get("recommended_plan", {}) if isinstance(package.get("recommended_plan"), dict) else {}
        items = selected.get("items", []) if package.get("status") == "completed" else []
        available = package.get("status") == "completed" and bool(package.get("recommended_strategy"))
        metrics = package.get("business_metrics", {}) if isinstance(package.get("business_metrics"), dict) else {}
        rows = [ProcurementRowBrief(
            ingredient_id=str(item.get("ingredient_id")), ingredient_name=ingredients.get(str(item.get("ingredient_id"))),
            supplier_id=item.get("supplier_id"), supplier_name=suppliers.get(item.get("supplier_id")),
            quantity=float(item.get("order_quantity", 0)), unit=item.get("unit"), pack_count=item.get("pack_count"),
            pack_size=_number(item.get("pack_size")), order_date=item.get("order_date"),
            arrival_date=item.get("arrival_date") or item.get("expected_arrival_date"),
            purchase_cost=_number(item.get("purchase_cost") if "purchase_cost" in item else item.get("line_cost")),
            reason_codes=derive_verified_order_reasons(item, package.get("reason_codes", [])),
        ) for item in items if float(item.get("order_quantity", 0)) > 0]
        demands = sorted((IngredientDemandBrief(
            ingredient_id=str(item.get("ingredient_id")), ingredient_name=ingredients.get(str(item.get("ingredient_id"))),
            target_date=item["target_date"], unit=item.get("unit"),
            p25=_number(item.get("p25")), p50=_number(item.get("p50")), p75=_number(item.get("p75")),
            contributions=item.get("contributions", []) if isinstance(item.get("contributions", []), list) else [],
        ) for item in package.get("ingredient_demand", [])), key=_ingredient_demand_sort_key)
        critic = package.get("critic", {}) if isinstance(package.get("critic"), dict) else {}
        hard = [str(item.get("code")) for item in critic.get("findings", []) if item.get("severity") == "error" and item.get("code")]
        risk = package.get("inventory_risk", {}) if isinstance(package.get("inventory_risk"), dict) else {}
        return DecisionBriefFacts(
            decision_run_id=run.decision_run_id, store_id=run.store_id, status=run.status,
            forecast=ForecastBrief(forecast_run_id=run.forecast_run_id, model_version=(forecast.model_version if forecast else None), horizon_days=run.horizon_days, cutoff_date=run.as_of_date),
            recommendation=RecommendationBrief(available=available, strategy=(package.get("recommended_strategy") if available else None), summary=("Persisted production recommendation." if available else None), total_purchase_cost=_number(metrics.get("purchase_cost") or metrics.get("total_purchase_cost")), expected_fill_rate=_number(metrics.get("fill_rate") or metrics.get("expected_fill_rate"))),
            procurement_rows=rows, ingredient_demand=demands,
            risk=RiskBrief(stockout_probability=None, expected_fill_rate=_number(metrics.get("fill_rate") or metrics.get("expected_fill_rate")), shortage_quantity=_number(metrics.get("shortage_quantity") or metrics.get("projected_shortage_quantity")), waste_quantity=_number(metrics.get("waste_quantity") or metrics.get("projected_waste_quantity"))),
            critic=CriticBrief(hard_violations=hard, warnings=[str(value) for value in critic.get("warnings", [])]),
            data_availability={"stockout_probability": "UNAVAILABLE", "forecast_model": "AVAILABLE" if forecast else "UNAVAILABLE", "ingredient_demand": "AVAILABLE" if demands else "UNAVAILABLE"}, generated_at=datetime.now(timezone.utc),
        )


def _number(value):
    return None if value is None else float(value)


def _ingredient_demand_sort_key(item: IngredientDemandBrief) -> tuple[str, str, str]:
    """Keep the persisted daily grain while making the outward API deterministic."""
    target_date = item.target_date.isoformat() if isinstance(item.target_date, date) else str(item.target_date)
    return (target_date, item.ingredient_id, item.ingredient_name or "")
