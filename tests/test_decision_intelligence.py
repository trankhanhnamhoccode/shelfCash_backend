import json
from datetime import date, datetime, timezone

from app.models.business import IngredientModel, SupplierModel
from app.models.decision import DecisionRunModel


def _run(run_id, package):
    return DecisionRunModel(decision_run_id=run_id, store_id="STORE_001", forecast_run_id="missing-forecast", as_of_date=date(2026, 8, 19), horizon_days=7, engine_mode="deterministic", status=package["status"], scenario_method="test", scenario_count=1, random_seed=42, recommended_strategy=package.get("recommended_strategy"), request_json="{}", package_json=json.dumps(package), warnings_json="[]", created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc))


def test_brief_no_feasible_never_creates_order_rows(client, monkeypatch):
    sf = client.app.state.session_factory
    package = {"decision_run_id": "no-feasible", "store_id": "STORE_001", "status": "completed_with_no_feasible_recommendation", "recommended_strategy": None, "recommended_plan": {"items": [{"ingredient_id": "must-not-appear", "order_quantity": 10}]}, "ingredient_demand": [], "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [{"code": "BUDGET", "severity": "error"}], "warnings": ["NO_PLAN"]}, "reason_codes": [], "warnings": []}
    with sf() as session:
        session.add(_run("no-feasible", package)); session.commit()
    response = client.get("/api/v1/decision-runs/no-feasible/brief")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation"]["available"] is False
    assert body["procurement_rows"] == []
    explanation = client.post("/api/v1/decision-runs/no-feasible/explanation", json={"language": "vi", "detail_level": "simple"})
    assert explanation.status_code == 200
    assert explanation.json()["grounded"] is True
    from app.decision_intelligence.adapter import ShelfCashDecisionIntelligenceAdapter
    monkeypatch.setattr(ShelfCashDecisionIntelligenceAdapter, "explain", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("M6 unavailable")))
    fallback = client.post("/api/v1/decision-runs/no-feasible/explanation", json={"language": "vi", "detail_level": "simple"})
    assert fallback.status_code == 200
    assert fallback.json()["source"] == "template"


def test_brief_preserves_supplier_quantity_and_ingredient_separation(client):
    sf = client.app.state.session_factory
    with sf() as session:
        session.add_all([
            IngredientModel(ingredient_id="di-milk", store_id="STORE_001", ingredient="Milk", normalized_name="di-milk", base_unit="lít", active=True, source="test"),
            IngredientModel(ingredient_id="di-tea", store_id="STORE_001", ingredient="Tea", normalized_name="di-tea", base_unit="kg", active=True, source="test"),
            SupplierModel(supplier_id="di-supplier", store_id="STORE_001", supplier="Dairy supplier", normalized_name="di-supplier", active=True, source="test"),
        ])
        package = {"decision_run_id": "di-feasible", "store_id": "STORE_001", "status": "completed", "recommended_strategy": "balanced", "recommended_plan": {"items": [{"ingredient_id": "di-milk", "supplier_id": "di-supplier", "order_quantity": 24, "unit": "lít", "pack_count": 2, "pack_size": 12, "line_cost": 100, "reason_codes": ["MOQ_CONSTRAINT"]}, {"ingredient_id": "di-tea", "supplier_id": "di-supplier", "order_quantity": 3, "unit": "kg", "line_cost": 30}]}, "ingredient_demand": [{"ingredient_id": "di-milk", "unit": "lít", "p25": 10, "p50": 20, "p75": 30, "contributions": []}, {"ingredient_id": "di-tea", "unit": "kg", "p25": 1, "p50": 2, "p75": 3, "contributions": []}], "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": ["WATCH"]}, "reason_codes": [], "warnings": []}
        session.add(_run("di-feasible", package)); session.commit()
    brief = client.get("/api/v1/decision-runs/di-feasible/brief").json()
    assert [(row["ingredient_id"], row["quantity"]) for row in brief["procurement_rows"]] == [("di-milk", 24.0), ("di-tea", 3.0)]
    assert brief["procurement_rows"][0]["supplier_name"] == "Dairy supplier"
    assert brief["procurement_rows"][0]["reason_codes"] == ["MOQ_CONSTRAINT"]
    assert brief["critic"]["warnings"] == ["WATCH"]


def test_what_if_invalid_mutations_return_422(client):
    for payload in ({"demand_multiplier": -1}, {"supplier_delay_days": -3}, {"budget_limit": -100}, {"strategy": "unknown"}):
        response = client.post("/api/v1/decision-runs/does-not-matter/what-if", json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
