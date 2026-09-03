import json
from datetime import date, datetime, timezone

import pytest

from app.config import Settings
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
        package = {"decision_run_id": "di-feasible", "store_id": "STORE_001", "status": "completed", "recommended_strategy": "balanced", "recommended_plan": {"items": [{"ingredient_id": "di-milk", "supplier_id": "di-supplier", "order_quantity": 24, "unit": "lít", "pack_count": 2, "pack_size": 12, "line_cost": 100, "reason_codes": ["MOQ_CONSTRAINT"]}, {"ingredient_id": "di-tea", "supplier_id": "di-supplier", "order_quantity": 3, "unit": "kg", "line_cost": 30}]}, "ingredient_demand": [{"ingredient_id": "di-milk", "target_date": "2026-08-20", "unit": "lít", "p25": 10, "p50": 20, "p75": 30, "contributions": []}, {"ingredient_id": "di-tea", "target_date": "2026-08-20", "unit": "kg", "p25": 1, "p50": 2, "p75": 3, "contributions": []}], "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": ["WATCH"]}, "reason_codes": [], "warnings": []}
        session.add(_run("di-feasible", package)); session.commit()
    brief = client.get("/api/v1/decision-runs/di-feasible/brief").json()
    assert [(row["ingredient_id"], row["quantity"]) for row in brief["procurement_rows"]] == [("di-milk", 24.0), ("di-tea", 3.0)]
    assert brief["procurement_rows"][0]["supplier_name"] == "Dairy supplier"
    assert brief["procurement_rows"][0]["reason_codes"] == ["MOQ_CONSTRAINT"]
    assert brief["critic"]["warnings"] == ["WATCH"]


def test_brief_keeps_daily_ingredient_demand_dates_order_and_evidence_identity(client):
    sf = client.app.state.session_factory
    dates = [f"2026-08-{day:02d}" for day in range(20, 27)]
    package = {"decision_run_id": "daily-demand", "store_id": "STORE_001", "status": "completed", "recommended_strategy": "balanced", "recommended_plan": {"items": []}, "ingredient_demand": [
        *[{"ingredient_id": "matcha", "target_date": day, "unit": "kg", "p25": 0.1, "p50": 0.2, "p75": 0.3, "contributions": []} for day in reversed(dates)],
        {"ingredient_id": "milk", "target_date": "2026-08-22", "unit": "lít", "p25": 1, "p50": 2, "p75": 3, "contributions": []},
    ], "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []}, "reason_codes": [], "warnings": []}
    with sf() as session:
        session.add_all([
            IngredientModel(ingredient_id="matcha", store_id="STORE_001", ingredient="Bột matcha", normalized_name="matcha", base_unit="kg", active=True, source="test"),
            IngredientModel(ingredient_id="milk", store_id="STORE_001", ingredient="Milk", normalized_name="milk", base_unit="lít", active=True, source="test"),
            _run("daily-demand", package),
        ])
        session.commit()

    brief = client.get("/api/v1/decision-runs/daily-demand/brief").json()
    matcha_rows = [row for row in brief["ingredient_demand"] if row["ingredient_id"] == "matcha"]
    assert [row["target_date"] for row in matcha_rows] == dates
    assert len({row["target_date"] for row in matcha_rows}) == 7
    assert brief["ingredient_demand"] == sorted(brief["ingredient_demand"], key=lambda row: (row["target_date"], row["ingredient_id"], row["ingredient_name"] or ""))
    assert {(row["ingredient_id"], row["target_date"]) for row in brief["ingredient_demand"]} == {(row["ingredient_id"], row["target_date"]) for row in package["ingredient_demand"]}

    explanation = client.post("/api/v1/decision-runs/daily-demand/explanation", json={"language": "en", "detail_level": "simple", "question": "matcha demand"}).json()
    demand_claims = [claim for claim in explanation["claims"] if claim["type"] == "INGREDIENT_DEMAND"]
    assert demand_claims
    assert all("target_date" in claim["value"] for claim in demand_claims)
    evidence = client.get("/api/v1/decision-runs/daily-demand/brief").json()["evidence"]
    demand_evidence = [item for item in evidence if item["entities"].get("ingredient_id") == "matcha"]
    assert len({item["entities"]["target_date"] for item in demand_evidence}) == 7
    assert len({item["evidence_id"] for item in demand_evidence}) == 7


def test_ingredient_synthesis_diagnostics_stay_in_decision_package_not_brief(client):
    sf = client.app.state.session_factory
    diagnostics = {
        "task": "ingredient_synthesis", "raw_response": '{"items": []}',
        "failure_stage": "JSON_PARSE", "items": [{"ingredient_id": "milk", "status": "fallback"}],
    }
    package = {
        "decision_run_id": "internal-synthesis-diagnostics", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": None, "recommended_plan": {"items": []}, "ingredient_demand": [],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [], "assistant": {"ingredient_synthesis_diagnostics": diagnostics},
    }
    with sf() as session:
        session.add(_run("internal-synthesis-diagnostics", package)); session.commit()
    stored = client.get("/api/v1/decision-runs/internal-synthesis-diagnostics")
    brief = client.get("/api/v1/decision-runs/internal-synthesis-diagnostics/brief")
    assert stored.status_code == 200 and stored.json()["assistant"]["ingredient_synthesis_diagnostics"] == diagnostics
    assert brief.status_code == 200
    assert "ingredient_synthesis_diagnostics" not in brief.json()
    assert diagnostics["raw_response"] not in brief.text


def _strategy_expression_package(run_id="strategy-expression-read"):
    return {
        "decision_run_id": run_id, "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "protected", "recommended_plan": {"items": []}, "ingredient_demand": [],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [],
        "strategies": {"protected": {"is_feasible": True, "purchase_cost": 4_680_000, "critic": {"findings": [], "warnings": []}}},
        "strategy_selection": {"rule": "lowest_exact_valid_candidate_cost_then_strategy_name", "selected_strategy": "protected", "eligible_candidates": ["protected"]},
    }


def test_strategy_expression_brief_default_is_deterministic_and_never_persists_expression_artifacts(client, caplog):
    """Default Brief reads render IS-4.4 without inspecting the provider."""
    package = _strategy_expression_package()
    with client.app.state.session_factory() as session:
        session.add(_run("strategy-expression-read", package)); session.commit()
    before = client.get("/api/v1/decision-runs/strategy-expression-read").json()
    import logging
    caplog.set_level(logging.INFO, logger="shelfcash.planning")
    response = client.get("/api/v1/decision-runs/strategy-expression-read/brief")
    after = client.get("/api/v1/decision-runs/strategy-expression-read").json()
    assert response.status_code == 200
    assert after == before
    assert "strategy_expression" not in after.get("assistant", {})
    event = next(record.message for record in caplog.records if "event=strategy_presentation.completed" in record.message)
    assert "mode=deterministic" in event and "source=deterministic" in event


def test_strategy_expression_default_uses_zero_calls_with_available_unavailable_and_throwing_providers(client):
    class Provider:
        def __init__(self, available=True, throws=False):
            self.available, self.throws, self.calls = available, throws, 0

        def generate_json_sync(self, *_args, **_kwargs):
            self.calls += 1
            if self.throws:
                raise AssertionError("deterministic strategy rendering must not call a provider")
            return {"strategies": []}

    sf = client.app.state.session_factory
    with sf() as session:
        for run_id in ("strategy-default-available", "strategy-default-unavailable", "strategy-default-throwing"):
            session.add(_run(run_id, _strategy_expression_package(run_id)))
        session.commit()

    service = client.app.state.decision_planning_service
    service.settings.strategy_expression_mode = "deterministic"
    expected = None
    for run_id, provider in (
        ("strategy-default-available", Provider()),
        ("strategy-default-unavailable", Provider(available=False)),
        ("strategy-default-throwing", Provider(throws=True)),
    ):
        service.llm_provider = provider
        first = client.get(f"/api/v1/decision-runs/{run_id}/brief")
        second = client.get(f"/api/v1/decision-runs/{run_id}/brief")
        assert first.status_code == 200 and second.status_code == 200
        presentations = [item["presentation"] for item in first.json()["strategy_evaluations"]]
        assert presentations == [item["presentation"] for item in second.json()["strategy_evaluations"]]
        assert provider.calls == 0
        expected = expected or presentations
        assert presentations == expected


def test_strategy_expression_explicit_llm_polish_uses_validated_qwen_or_whole_set_fallback(client, caplog):
    class Provider:
        available = True

        def __init__(self, response):
            self.response, self.calls = response, 0

        def generate_json_sync(self, *_args, **_kwargs):
            self.calls += 1
            return self.response

    sf = client.app.state.session_factory
    with sf() as session:
        session.add(_run("strategy-polish-valid", _strategy_expression_package("strategy-polish-valid")))
        session.add(_run("strategy-polish-invalid", _strategy_expression_package("strategy-polish-invalid")))
        session.commit()

    service = client.app.state.decision_planning_service
    canonical = client.get("/api/v1/decision-runs/strategy-polish-valid/brief").json()["strategy_evaluations"]
    valid = Provider({"strategies": [{
        "strategy": item["strategy"],
        "headline": item["presentation"]["headline"],
        "summary": item["presentation"]["summary"],
        "reason_messages": item["presentation"]["reason_messages"],
    } for item in canonical]})
    import logging
    caplog.set_level(logging.INFO, logger="shelfcash.planning")
    service.settings.strategy_expression_mode = "llm_polish"
    service.llm_provider = valid
    polished = client.get("/api/v1/decision-runs/strategy-polish-valid/brief")
    assert polished.status_code == 200 and valid.calls == 1
    assert polished.json()["strategy_evaluations"] == canonical
    assert any("event=strategy_expression.completed" in record.message and "status=success" in record.message and "source=llm" in record.message for record in caplog.records)

    invalid = Provider({"strategies": []})
    service.llm_provider = invalid
    fallback = client.get("/api/v1/decision-runs/strategy-polish-invalid/brief")
    assert fallback.status_code == 200 and invalid.calls == 1
    # Restore deterministic mode before obtaining the canonical baseline so the
    # optional read-time polish path cannot participate in this assertion.
    service.settings.strategy_expression_mode = "deterministic"
    canonical = client.get("/api/v1/decision-runs/strategy-polish-invalid/brief").json()["strategy_evaluations"]
    assert fallback.json()["strategy_evaluations"] == canonical


def test_strategy_expression_mode_rejects_unknown_value():
    assert Settings(_env_file=None).strategy_expression_mode == "deterministic"
    with pytest.raises(ValueError):
        Settings(strategy_expression_mode="llm-polsh")


def test_legacy_brief_read_reconstructs_ingredient_synthesis_without_provider_call(client):
    class Provider:
        available = True
        calls = 0
        async def generate_json(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("legacy brief reads must not invoke the provider")

    package = {
        "decision_run_id": "legacy-synthesis-read", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [{"ingredient_id": "legacy-ingredient", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3, "contributions": []}],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("legacy-synthesis-read", package)); session.commit()
    provider = Provider()
    client.app.state.decision_planning_service.llm_provider = provider
    response = client.get("/api/v1/decision-runs/legacy-synthesis-read/brief")
    assert response.status_code == 200
    assert response.json()["ingredient_synthesis"][0]["source"] == "rule_based"
    assert provider.calls == 0


def test_default_ingredient_synthesis_is_persisted_and_brief_reads_it_without_provider_call(client):
    class ThrowingProvider:
        available = True

        def __init__(self):
            self.calls = 0

        async def generate_json(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("deterministic ingredient synthesis must not call a provider")

    package = {
        "decision_run_id": "persisted-deterministic-synthesis", "store_id": "STORE_001",
        "status": "completed_with_no_feasible_recommendation", "recommended_strategy": None,
        "recommended_plan": {"items": []},
        "ingredient_demand": [{"ingredient_id": "persisted-ingredient", "target_date": "2026-08-21", "unit": "kg", "p25": 1, "p50": 2, "p75": 3, "contributions": []}],
        "business_metrics": {}, "inventory_risk": {}, "critic": {"findings": [], "warnings": []},
        "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("persisted-deterministic-synthesis", package)); session.commit()

    service = client.app.state.decision_planning_service
    provider = ThrowingProvider()
    service.llm_provider = provider
    service._generate_and_persist_overall_summary("persisted-deterministic-synthesis")

    stored = client.get("/api/v1/decision-runs/persisted-deterministic-synthesis").json()
    persisted = stored["assistant"]["ingredient_synthesis"]
    assert persisted[0]["source"] == "rule_based"
    brief = client.get("/api/v1/decision-runs/persisted-deterministic-synthesis/brief").json()
    assert brief["ingredient_synthesis"] == persisted
    assert provider.calls == 0


def test_what_if_invalid_mutations_return_422(client):
    for payload in ({"demand_multiplier": -1}, {"supplier_delay_days": -3}, {"budget_limit": -100}, {"strategy": "unknown"}):
        response = client.post("/api/v1/decision-runs/does-not-matter/what-if", json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
