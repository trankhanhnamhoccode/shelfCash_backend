import json
from datetime import date, datetime, timezone

from app.decision_intelligence.contracts import (
    CriticBrief,
    DecisionBriefFacts,
    ForecastBrief,
    IngredientDemandBrief,
    ProcurementRowBrief,
    RecommendationBrief,
    RiskBrief,
)
from app.decision_intelligence.overall_summary import OverallSummaryProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.models.decision import DecisionRunModel


def _brief() -> DecisionBriefFacts:
    return DecisionBriefFacts(
        decision_run_id="overall-summary-run",
        store_id="STORE_001",
        status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(
            available=True,
            strategy="balanced",
            total_purchase_cost=8_338_000,
        ),
        procurement_rows=[
            ProcurementRowBrief(
                ingredient_id="banana", ingredient_name="Chuoi", quantity=30,
                unit="kg", pack_count=6, pack_size=5,
                reason_codes=["PACK_SIZE_ROUNDING"],
            )
        ],
        ingredient_demand=[
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 23), p25=8, p50=9.45, p75=10.5),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 21), p25=8, p50=9.5, p75=10),
            IngredientDemandBrief(ingredient_id="banana", ingredient_name="Chuoi", unit="kg", target_date=date(2026, 8, 22), p25=9, p50=10, p75=11),
        ],
        risk=RiskBrief(stockout_probability=None),
        critic=CriticBrief(),
        generated_at=datetime.now(timezone.utc),
    )


class _Gateway:
    available = True

    def __init__(self, factory):
        self.factory = factory
        self.calls = 0
        self.payload = None

    async def generate_json(self, _system, payload, **_kwargs):
        self.calls += 1
        self.payload = payload
        return self.factory(payload)


def _valid_response(payload):
    by_type = {item["type"]: item for item in payload["evidence"]}
    overview = by_type["PLAN_OVERVIEW"]["evidence_id"]
    alignment = by_type["DEMAND_ORDER_ALIGNMENT"]["evidence_id"]
    headline = {
        "type": "PLAN_OVERVIEW",
        "text": "Ke hoach hien tai su dung chien luoc Can bang.",
        "evidence_ids": [overview],
    }
    comparison = {
        "type": "DEMAND_ORDER_ALIGNMENT",
        "text": "Luong dat 30 kg cao hon tong nhu cau trung vi 28,95 kg khoang 1,05 kg.",
        "evidence_ids": [alignment],
    }
    return {
        "headline": headline,
        "summary": comparison,
        "key_points": [],
        "warning_summary": None,
        "used_evidence_ids": [overview, alignment],
    }


def test_overall_summary_accepts_grounded_observation_and_derived_comparison():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)
    gateway = _Gateway(_valid_response)

    summary = OverallSummaryProvider(gateway, None).summarize(brief, facts)

    assert gateway.calls == 1
    assert summary.source == "llm"
    assert summary.grounded is True
    assert "1,05" in summary.summary
    assert gateway.payload["communication_plan"]["decision"]
    assert gateway.payload["communication_plan"]["main_attention"] == []


def test_overall_summary_falls_back_for_malformed_or_unsupported_causal_output():
    brief = _brief()
    facts = DecisionSemanticEvidenceBuilder().build(brief)

    malformed = OverallSummaryProvider(_Gateway(lambda _payload: {"bad": "response"}), None).summarize(brief, facts)
    assert malformed.source == "deterministic_fallback"

    def unsupported(payload):
        response = _valid_response(payload)
        alignment = next(item for item in payload["evidence"] if item["type"] == "DEMAND_ORDER_ALIGNMENT")
        response["summary"] = {
            "type": "DEMAND_ORDER_ALIGNMENT",
            "text": "30 kg duoc dat do quy cach dong goi 5 kg.",
            "evidence_ids": [alignment["evidence_id"]],
        }
        response["used_evidence_ids"] = [
            response["headline"]["evidence_ids"][0], alignment["evidence_id"],
        ]
        return response

    rejected = OverallSummaryProvider(_Gateway(unsupported), None).summarize(brief, facts)
    assert rejected.source == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in rejected.summary
    assert "quy cach" not in rejected.summary.lower()

    def raw_machine_code(payload):
        response = _valid_response(payload)
        response["summary"] = {
            "type": "DEMAND_ORDER_ALIGNMENT",
            "text": "PACK_SIZE_ROUNDING applies to the plan.",
            "evidence_ids": [next(item for item in payload["evidence"] if item["type"] == "DEMAND_ORDER_ALIGNMENT")["evidence_id"]],
        }
        response["used_evidence_ids"] = [
            response["headline"]["evidence_ids"][0], response["summary"]["evidence_ids"][0],
        ]
        return response

    raw_code = OverallSummaryProvider(_Gateway(raw_machine_code), None).summarize(brief, facts)
    assert raw_code.source == "deterministic_fallback"
    assert "PACK_SIZE_ROUNDING" not in raw_code.summary


def test_deterministic_summary_never_invents_null_risk_or_internal_identifiers():
    brief = _brief()
    summary = OverallSummaryProvider(None, None).deterministic_fallback(
        brief, DecisionSemanticEvidenceBuilder().build(brief),
    )

    rendered = " ".join([summary.headline, summary.summary, *summary.key_points, summary.warning_summary or ""])
    assert "overall-summary-run" not in rendered
    assert "PACK_SIZE_ROUNDING" not in rendered
    assert "0%" not in rendered


def test_no_feasible_recommendation_has_a_grounded_summary_without_order_metrics():
    brief = _brief().model_copy(update={
        "status": "completed_with_no_feasible_recommendation",
        "recommendation": RecommendationBrief(available=False),
        "procurement_rows": [],
    })
    summary = OverallSummaryProvider(None, None).deterministic_fallback(
        brief, DecisionSemanticEvidenceBuilder().build(brief),
    )

    assert summary.source == "deterministic_fallback"
    assert summary.grounded is True
    assert "30" not in summary.summary
    assert "8.338" not in summary.summary


def _run(run_id: str, package: dict) -> DecisionRunModel:
    return DecisionRunModel(
        decision_run_id=run_id,
        store_id="STORE_001",
        forecast_run_id="missing-forecast",
        as_of_date=date(2026, 8, 20),
        horizon_days=7,
        engine_mode="deterministic",
        status=package["status"],
        scenario_method="test",
        scenario_count=1,
        random_seed=42,
        recommended_strategy=package.get("recommended_strategy"),
        request_json="{}",
        package_json=json.dumps(package),
        warnings_json="[]",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def test_brief_adds_canonical_horizon_summary_and_old_run_fallback_without_gateway(client):
    package = {
        "decision_run_id": "phase2-old-run",
        "store_id": "STORE_001",
        "status": "completed",
        "recommended_strategy": "balanced",
        "recommended_plan": {"items": [{"ingredient_id": "banana", "order_quantity": 30, "unit": "kg"}]},
        "ingredient_demand": [
            {"ingredient_id": "banana", "target_date": "2026-08-23", "unit": "kg", "p25": 8, "p50": 9.45, "p75": 10.5},
            {"ingredient_id": "banana", "target_date": "2026-08-21", "unit": "kg", "p25": 8, "p50": 9.5, "p75": 10},
            {"ingredient_id": "banana", "target_date": "2026-08-22", "unit": "kg", "p25": 9, "p50": 10, "p75": 11},
        ],
        "business_metrics": {"projected_purchase_cost": 8_338_000},
        "inventory_risk": {},
        "critic": {"findings": [], "warnings": []},
        "reason_codes": [],
        "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("phase2-old-run", package))
        session.commit()

    first = client.get("/api/v1/decision-runs/phase2-old-run/brief")
    second = client.get("/api/v1/decision-runs/phase2-old-run/brief")
    assert first.status_code == second.status_code == 200
    body = first.json()
    assert [row["target_date"] for row in body["ingredient_demand"]] == ["2026-08-21", "2026-08-22", "2026-08-23"]
    assert body["ingredient_demand_summary"] == [{
        "ingredient_id": "banana", "ingredient_name": None, "unit": "kg",
        "period_start": "2026-08-21", "period_end": "2026-08-23",
        "p25_total": 25.0, "p50_total": 28.95, "p75_total": 31.5,
        "daily_p50_min": 9.45, "daily_p50_max": 10.0,
        "peak_date": "2026-08-22", "peak_p50": 10.0,
        "aggregation_method": "sum_daily_quantiles",
    }]
    assert body["assistant_summary"]["source"] == "deterministic_fallback"
    assert "assistant" not in client.get("/api/v1/decision-runs/phase2-old-run").json()


def test_summary_generation_is_not_repeated_by_brief_reads(client, monkeypatch):
    from app.decision_intelligence.overall_summary import OverallSummaryProvider

    calls = 0
    original = OverallSummaryProvider.summarize

    def counted(self, brief, facts):
        nonlocal calls
        calls += 1
        return original(self, brief, facts)

    monkeypatch.setattr(OverallSummaryProvider, "summarize", counted)
    package = {
        "decision_run_id": "phase2-persisted-run", "store_id": "STORE_001", "status": "completed",
        "recommended_strategy": "balanced", "recommended_plan": {"items": []},
        "ingredient_demand": [], "business_metrics": {}, "inventory_risk": {},
        "critic": {"findings": [], "warnings": []}, "reason_codes": [], "warnings": [],
    }
    with client.app.state.session_factory() as session:
        session.add(_run("phase2-persisted-run", package))
        session.commit()

    service = client.app.state.decision_planning_service
    service._generate_and_persist_overall_summary("phase2-persisted-run")
    client.get("/api/v1/decision-runs/phase2-persisted-run/brief")
    client.get("/api/v1/decision-runs/phase2-persisted-run/brief")
    assert calls == 1
