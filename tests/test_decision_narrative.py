import asyncio
from datetime import date, datetime, timezone

from app.config import Settings
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider, aggregate_evidence


class MockQwen:
    available = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_json(self, system, payload, **kwargs):
        self.calls.append(payload)
        return self.response(payload)


def brief(days=1):
    demand = [IngredientDemandBrief(ingredient_id="milk", ingredient_name="Sữa tươi", target_date=date(2026, 8, 20 + day), unit="lít", p25=1, p50=2 + day, p75=3, contributions=[]) for day in range(days)]
    return DecisionBriefFacts(
        decision_run_id="narrative-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(forecast_run_id="forecast", horizon_days=days, cutoff_date=date(2026, 8, 19)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="milk", ingredient_name="Sữa tươi", supplier_id="supplier", quantity=60, unit="lít", reason_codes=[])],
        ingredient_demand=demand, risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def settings():
    return Settings(openrouter_api_key="mock-key")


def test_qwen_narrative_accepts_supported_quantity():
    def response(payload):
        order = next(item for item in payload["evidence"] if item["type"] == "PROCUREMENT_QUANTITY")
        return {"answer": "Kế hoạch ghi nhận đặt 60 lít Sữa tươi.", "claims": [{"type": "PROCUREMENT_QUANTITY", "text": "Kế hoạch ghi nhận đặt 60 lít Sữa tươi.", "evidence_ids": [order["evidence_id"]]}], "used_evidence_ids": [order["evidence_id"]]}
    result = DecisionNarrativeProvider(MockQwen(response), settings()).explain(brief(), question="Tại sao phải nhập Sữa tươi?", language="vi", detail_level="simple")
    assert result.provider == "openrouter_qwen" and result.grounded is True
    assert result.claims[0].evidence_ids
    assert result.raw_response is not None
    assert result.raw_response["answer"] == "Kế hoạch ghi nhận đặt 60 lít Sữa tươi."


def test_qwen_unsupported_number_and_entity_fall_back():
    def response(payload):
        order = next(item for item in payload["evidence"] if item["type"] == "PROCUREMENT_QUANTITY")
        return {"answer": "Nhập 70 lít Chuối.", "claims": [{"type": "PROCUREMENT_QUANTITY", "text": "Nhập 70 lít Chuối.", "evidence_ids": [order["evidence_id"]]}]}
    result = DecisionNarrativeProvider(MockQwen(response), settings()).explain(brief(), question="Sữa tươi", language="vi", detail_level="simple")
    assert result.provider == "deterministic_fallback"


def test_qwen_unsupported_safety_stock_cause_falls_back():
    def response(payload):
        order = next(item for item in payload["evidence"] if item["type"] == "PROCUREMENT_QUANTITY")
        return {"answer": "Cần nhập để duy trì tồn an toàn.", "claims": [{"type": "PROCUREMENT_QUANTITY", "text": "Cần nhập để duy trì tồn an toàn.", "evidence_ids": [order["evidence_id"]]}]}
    result = DecisionNarrativeProvider(MockQwen(response), settings()).explain(brief(), question="Sữa tươi", language="vi", detail_level="simple")
    assert result.provider == "deterministic_fallback"


def test_qwen_malformed_and_unavailable_fall_back():
    malformed = MockQwen(lambda payload: {"answer": "not structured", "claims": "bad"})
    assert DecisionNarrativeProvider(malformed, settings()).explain(brief(), question="Sữa tươi", language="vi", detail_level="simple").provider == "deterministic_fallback"
    disabled = type("Disabled", (), {"available": False})()
    assert DecisionNarrativeProvider(disabled, settings()).explain(brief(), question="Sữa tươi", language="vi", detail_level="simple").provider == "shelfcash_decision_intelligence"


def test_daily_aggregation_is_deterministic():
    source = DecisionNarrativeProvider(MockQwen(lambda _: {}), settings()).deterministic
    evidence = source._evidence(brief(days=7))
    aggregated = aggregate_evidence(brief(days=7), evidence.items)
    summary = next(item for item in aggregated if item["type"] == "DEMAND_HORIZON_SUMMARY")
    assert summary["p50_total"] == sum(range(2, 9))
    assert summary["peak_date"] == "2026-08-26"
    assert len([item for item in aggregated if item["type"] == "DEMAND_DAILY"]) == 7
