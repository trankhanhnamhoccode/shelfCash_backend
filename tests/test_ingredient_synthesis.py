from datetime import date, datetime, timezone
import asyncio
import httpx
import pytest
from app.config import Settings
from app.core.exceptions import LLMProviderError
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.ingredient_synthesis import IngredientSynthesisProvider
from app.decision_intelligence.overall_summary import OverallSummaryProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.decision_intelligence.warning_presentation import present_warnings
from app.llm.openrouter_qwen import OpenRouterLLMGateway


def _brief():
    return DecisionBriefFacts(
        decision_run_id="ingredient-presentation", store_id="store", status="completed",
        forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="watch", ingredient_name="Tra", quantity=3, unit="kg")],
        ingredient_demand=[
            IngredientDemandBrief(ingredient_id="normal", ingredient_name="Sua", unit="lít", target_date=date(2026, 8, 21), p25=1, p50=1.25, p75=2),
            IngredientDemandBrief(ingredient_id="watch", ingredient_name="Tra", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
        ], risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )


def test_every_ingredient_has_deterministic_synthesis_and_display_values():
    brief = _brief()
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [{"ingredient_id": "watch", "unit": "kg", "shortage_quantity": 0.5, "stockout_event_count": 1}]}}, "warnings": []}
    facts = DecisionSemanticEvidenceBuilder().build(brief, package)
    result = IngredientSynthesisProvider(None, None).synthesize(brief, facts)
    assert [item.ingredient_id for item in result] == ["normal", "watch"]
    assert result[0].importance == "normal"
    assert result[0].source == "rule_based"
    assert "1,25" in result[0].summary
    assert result[1].importance == "watch"
    assert result[1].source == "rule_based"


def test_warning_presentation_is_deterministic_and_hides_unknown_machine_code():
    warnings = {item.code: item for item in present_warnings([
        "CAPACITY_NOT_EVALUATED", "STRESS_SHORTAGE_OBSERVED",
        "UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS", "SOME_NEW_CODE",
    ])}
    assert warnings["CAPACITY_NOT_EVALUATED"].audience == "user"
    assert warnings["CAPACITY_NOT_EVALUATED"].title == "Chưa thể đánh giá đầy đủ sức chứa kho"
    assert warnings["STRESS_SHORTAGE_OBSERVED"].title != "STRESS_SHORTAGE_OBSERVED"
    assert warnings["STRESS_SHORTAGE_OBSERVED"].title == "Có nguy cơ thiếu hàng trong một số tình huống"
    assert warnings["UNWEIGHTED_DESIGN_SCENARIOS_USE_EQUAL_CANDIDATE_WEIGHTS"].audience == "technical"
    assert warnings["SOME_NEW_CODE"].audience == "technical"
    assert warnings["SOME_NEW_CODE"].title != "SOME_NEW_CODE"


class _Gateway:
    available = True

    def __init__(self):
        self.calls = 0

    async def generate_json(self, _prompt, payload, **_kwargs):
        self.calls += 1
        items = []
        for ingredient in payload["ingredients"]:
            risk = next(item for item in ingredient["evidence"] if item["type"] == "INGREDIENT_OPERATIONAL_RISK")
            evidence_id = risk["evidence_id"]
            # The second item is deliberately invalid: it cites another item.
            used = [evidence_id] if ingredient["ingredient_id"] == "critical-a" else ["not-authorized"]
            items.append({
                "ingredient_id": ingredient["ingredient_id"], "headline": "Cần theo dõi", "summary": "Có thể thiếu hàng trong kỳ kế hoạch.",
                "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": "Có thể thiếu hàng trong kỳ kế hoạch.", "evidence_ids": used}],
                "used_evidence_ids": used,
            })
        return {"items": items}


def test_critical_ingredients_use_one_batch_and_invalid_item_alone_falls_back():
    brief = _brief().model_copy(update={
        "ingredient_demand": [
            IngredientDemandBrief(ingredient_id="critical-a", ingredient_name="A", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
            IngredientDemandBrief(ingredient_id="critical-b", ingredient_name="B", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
        ], "procurement_rows": [],
    })
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [
        {"ingredient_id": "critical-a", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
        {"ingredient_id": "critical-b", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
    ]}}, "warnings": []}
    gateway = _Gateway()
    provider = IngredientSynthesisProvider(gateway, None)
    result = provider.synthesize(brief, DecisionSemanticEvidenceBuilder().build(brief, package))
    assert gateway.calls == 1
    assert {item.ingredient_id: item.source for item in result} == {"critical-a": "llm", "critical-b": "deterministic_fallback"}
    diagnostics = provider.last_diagnostics
    assert diagnostics["status"] == "partial_success"
    assert {item["ingredient_id"]: item["failure_stage"] for item in diagnostics["items"]}["critical-b"] == "ENTITY_GROUNDING"


def _critical_batch():
    brief = _brief().model_copy(update={
        "ingredient_demand": [
            IngredientDemandBrief(ingredient_id="critical-a", ingredient_name="A", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
            IngredientDemandBrief(ingredient_id="critical-b", ingredient_name="B", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3),
        ], "procurement_rows": [],
    })
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [
        {"ingredient_id": "critical-a", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
        {"ingredient_id": "critical-b", "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"},
    ]}}, "warnings": []}
    return brief, DecisionSemanticEvidenceBuilder().build(brief, package)


class _ContextGateway:
    available = True

    async def generate_json(self, _prompt, payload, *, request_context, **_kwargs):
        request_context["openrouter_diagnostics"] = {
            "correlation_id": request_context["correlation_id"], "attempt_count": 1,
            "raw_response_present": True, "content_present": True,
            "resolved_model": "qwen/qwen3.5-9b", "resolved_provider": "mock-openrouter",
        }
        request_context["openrouter_raw_content"] = '{"items": "mocked"}'
        return {"items": [
            {"ingredient_id": entry["ingredient_id"], "headline": "Cần theo dõi", "summary": "Có thể thiếu hàng trong kỳ kế hoạch.",
             "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": "Có thể thiếu hàng trong kỳ kế hoạch.", "evidence_ids": entry["communication_plan"]["primary"]}],
             "used_evidence_ids": entry["communication_plan"]["primary"]}
            for entry in payload["ingredients"]
        ]}


def test_batch_diagnostics_capture_success_and_raw_provider_content():
    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_ContextGateway(), None)
    result = provider.synthesize(brief, facts)
    diagnostics = provider.last_diagnostics
    assert all(item.source == "llm" for item in result)
    assert diagnostics["llm_attempted"] is True
    assert diagnostics["provider_call_count"] == 1
    assert diagnostics["attempt_count"] == 1
    assert diagnostics["status"] == "success"
    assert diagnostics["failure_stage"] is None
    assert diagnostics["raw_response_present"] is True
    assert diagnostics["content_present"] is True
    assert diagnostics["raw_response"] == '{"items": "mocked"}'
    assert {item["status"] for item in diagnostics["items"]} == {"llm_success"}
    # Provider internals are not part of the manager-facing item contract.
    assert "failure_stage" not in result[0].model_dump(mode="json")


def test_batch_failure_records_provider_stage_and_each_eligible_fallback():
    class _TimeoutGateway:
        available = True

        async def generate_json(self, *_args, request_context, **_kwargs):
            request_context["openrouter_diagnostics"] = {"attempt_count": 2, "raw_response_present": False, "content_present": False}
            raise LLMProviderError("timeout", details={"failure_stage": "TIMEOUT"}, http_status=504)

    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_TimeoutGateway(), None)
    result = provider.synthesize(brief, facts)
    diagnostics = provider.last_diagnostics
    assert all(item.source == "deterministic_fallback" for item in result)
    assert diagnostics["status"] == "failed"
    assert diagnostics["failure_stage"] == "TIMEOUT"
    assert diagnostics["raw_response_present"] is False
    assert diagnostics["content_present"] is False
    assert {(item["status"], item["failure_stage"]) for item in diagnostics["items"]} == {("fallback", "TIMEOUT")}


def test_schema_failure_is_batch_level_not_obscured_as_item_grounding_failure():
    class _BadSchemaGateway(_ContextGateway):
        async def generate_json(self, *_args, request_context, **_kwargs):
            request_context["openrouter_diagnostics"] = {"attempt_count": 1, "raw_response_present": True, "content_present": True}
            request_context["openrouter_raw_content"] = '{"items": "not-a-list"}'
            return {"items": "not-a-list"}

    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_BadSchemaGateway(), None)
    result = provider.synthesize(brief, facts)
    assert all(item.source == "deterministic_fallback" for item in result)
    assert provider.last_diagnostics["failure_stage"] == "SCHEMA_VALIDATION"
    assert {item["failure_stage"] for item in provider.last_diagnostics["items"]} == {"SCHEMA_VALIDATION"}


def test_rate_limit_is_distinguished_from_generic_http_failure():
    class _RateLimitGateway:
        available = True

        async def generate_json(self, *_args, request_context, **_kwargs):
            request_context["openrouter_diagnostics"] = {"attempt_count": 2, "http_status": 429, "raw_response_present": True, "content_present": False}
            request_context["openrouter_raw_response"] = '{"error":"rate limited"}'
            raise LLMProviderError("rate limited", details={"failure_stage": "HTTP"}, http_status=429)

    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_RateLimitGateway(), None)
    provider.synthesize(brief, facts)
    assert provider.last_diagnostics["failure_stage"] == "RATE_LIMIT"
    assert provider.last_diagnostics["http_status"] == 429


def test_item_diagnostics_preserve_independent_numeric_and_causal_failures(monkeypatch):
    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_ContextGateway(), None)

    def guarded(_payload, _records, _evidence, _brief, _language, _detail, _intent, *, target_ingredient_id=None):
        if target_ingredient_id == "critical-a":
            raise ValueError("unsupported_numeric_claim")
        raise ValueError("unsupported_causal_claim")

    monkeypatch.setattr(provider.guard, "_guard", guarded)
    result = provider.synthesize(brief, facts)
    diagnostics = provider.last_diagnostics
    assert all(item.source == "deterministic_fallback" for item in result)
    assert diagnostics["status"] == "failed"
    assert diagnostics["failure_stage"] is None
    by_id = {item["ingredient_id"]: item for item in diagnostics["items"]}
    assert by_id["critical-a"]["failure_stage"] == "NUMERIC_GROUNDING"
    assert by_id["critical-b"]["failure_stage"] == "CAUSAL_GROUNDING"


def test_unavailable_provider_is_not_attempted_and_raw_response_is_redacted():
    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(None, None)
    result = provider.synthesize(brief, facts)
    assert all(item.source == "rule_based" for item in result)
    assert provider.last_diagnostics["llm_attempted"] is False
    assert provider.last_diagnostics["status"] == "not_attempted"
    assert provider.last_diagnostics["failure_stage"] == "PROVIDER_UNAVAILABLE"
    assert "secret" not in provider._safe_raw_response('Authorization: secret {"api_key": "another-secret"}')


def test_unclassified_transport_network_exception_is_not_reported_as_unknown():
    class _NetworkGateway:
        available = True

        async def generate_json(self, *_args, **_kwargs):
            raise httpx.ConnectError("synthetic network failure", request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))

    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_NetworkGateway(), None)
    result = provider.synthesize(brief, facts)

    assert all(item.source == "deterministic_fallback" for item in result)
    assert provider.last_diagnostics["failure_stage"] == "NETWORK"
    assert provider.last_diagnostics["exception_type"] == "ConnectError"
    assert provider.last_diagnostics["raw_response_present"] is False
    assert provider.last_diagnostics["content_present"] is False


def test_unclassified_internal_runtime_exception_is_observable_not_unknown():
    class _BrokenGateway:
        available = True

        async def generate_json(self, *_args, **_kwargs):
            raise RuntimeError("synthetic internal failure")

    brief, facts = _critical_batch()
    provider = IngredientSynthesisProvider(_BrokenGateway(), None)
    provider.synthesize(brief, facts)

    diagnostics = provider.last_diagnostics
    assert diagnostics["failure_stage"] == "INTERNAL_RUNTIME"
    assert diagnostics["exception_type"] == "RuntimeError"
    assert diagnostics["exception_message"] == "synthetic internal failure"


def test_old_temporary_loop_bridge_reuses_a_loop_bound_transport_after_close():
    class _LoopBoundTransport:
        def __init__(self):
            self.owner_loop = None

        async def post(self):
            loop = asyncio.get_running_loop()
            if self.owner_loop is None:
                self.owner_loop = loop
            if self.owner_loop.is_closed():
                raise RuntimeError("Event loop is closed")
            return "ok"

    transport = _LoopBoundTransport()

    def old_feature_bridge():
        return asyncio.run(transport.post())

    assert old_feature_bridge() == "ok"
    with pytest.raises(RuntimeError, match="Event loop is closed"):
        old_feature_bridge()


def test_shared_gateway_owner_loop_keeps_summary_and_repeated_synthesis_alive(monkeypatch):
    provider = OpenRouterLLMGateway(Settings(openrouter_api_key="mock-key"))

    async def mock_post(url, json, **_kwargs):
        task = json["response_format"]["json_schema"]["name"]
        payload = __import__("json").loads(json["messages"][1]["content"])
        if task == "plan_summary":
            overview = next(item for item in payload["evidence"] if item["type"] == "PLAN_OVERVIEW")
            claim = {"type": "PLAN_OVERVIEW", "text": "Ke hoach hien tai su dung chien luoc Can bang.", "evidence_ids": [overview["evidence_id"]]}
            response = {"headline": claim, "summary": claim, "key_points": [], "warning_summary": None, "used_evidence_ids": [overview["evidence_id"]]}
        else:
            response = {"items": [
                {
                    "ingredient_id": item["ingredient_id"], "headline": f"{item['evidence'][0].get('ingredient_name') or item['ingredient_id']} can theo doi",
                    "summary": f"{item['evidence'][0].get('ingredient_name') or item['ingredient_id']} co the thieu hang trong ky ke hoach.",
                    "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": f"{item['evidence'][0].get('ingredient_name') or item['ingredient_id']} co the thieu hang trong ky ke hoach.", "evidence_ids": item["communication_plan"]["primary"]}],
                    "used_evidence_ids": item["communication_plan"]["primary"],
                }
                for item in payload["ingredients"]
            ]}
        return httpx.Response(200, json={"id": "generation-test", "model": "qwen/qwen3.5-9b", "provider": "test-provider", "choices": [{"finish_reason": "stop", "message": {"content": __import__("json").dumps(response)}}]}, request=httpx.Request("POST", url))

    client = asyncio.run(provider._get_client())
    monkeypatch.setattr(client, "post", mock_post)
    summary_brief = _brief()
    summary = OverallSummaryProvider(provider, None).summarize(
        summary_brief, DecisionSemanticEvidenceBuilder().build(summary_brief),
    )
    brief, facts = _critical_batch()
    first = IngredientSynthesisProvider(provider, None)
    second = IngredientSynthesisProvider(provider, None)
    first_result = first.synthesize(brief, facts)
    second_result = second.synthesize(brief, facts)

    assert summary.source == "llm"
    assert any(item.source == "llm" for item in first_result)
    assert any(item.source == "llm" for item in second_result)
    assert first.last_diagnostics["provider"] == "test-provider"
    assert first.last_diagnostics["resolved_model"] == "qwen/qwen3.5-9b"
    assert first.last_diagnostics["raw_response_present"] is True
    assert first.last_diagnostics["content_present"] is True
    asyncio.run(provider.close())
