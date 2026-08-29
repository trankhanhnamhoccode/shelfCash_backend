from datetime import date, datetime, timezone

import pytest

from app.core.exceptions import LLMProviderError
from app.decision_intelligence.contracts import CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief, RecommendationBrief, RiskBrief, RiskDetail
from app.decision_intelligence.ingredient_synthesis import IngredientSynthesisProvider
from app.decision_intelligence.semantic_evidence import DecisionSemanticEvidenceBuilder
from app.decision_intelligence.style_examples import retrieve_style_examples


def brief(ids, critical=(), warning=()):
    details = [RiskDetail(code="RISK_CONSTRAINT_VIOLATION", classification="risk", category="risk_evaluation", severity="critical", title="Critical", scope="ingredient", ingredient_id=x, source_count=1) for x in critical]
    details += [RiskDetail(code="STRESS_SHORTAGE_OBSERVED", classification="risk", category="shortage", severity="warning", title="Watch", scope="ingredient", ingredient_id=x, source_count=1) for x in warning]
    return DecisionBriefFacts(decision_run_id="ingredient-run", store_id="store", status="completed", forecast=ForecastBrief(horizon_days=7, cutoff_date=date(2026, 8, 20)), recommendation=RecommendationBrief(available=True, strategy="balanced"), ingredient_demand=[IngredientDemandBrief(ingredient_id=x, ingredient_name=f"Ingredient {x}", unit="kg", target_date=date(2026, 8, 21), p25=1, p50=2, p75=3) for x in ids], risk=RiskBrief(), critic=CriticBrief(), risk_details=details, generated_at=datetime.now(timezone.utc))


def facts(value, operational=()):
    package = {"business_metrics": {"deterministic": {"ingredient_metrics": [{"ingredient_id": x, "unit": "kg", "shortage_quantity": 1, "stockout_event_count": 1, "first_stockout_date": "2026-08-21"} for x in operational]}}, "warnings": []}
    return DecisionSemanticEvidenceBuilder().build(value, package)


class Gateway:
    available = True
    def __init__(self, failures=None, responses=None):
        self.calls, self.failures, self.responses = [], failures or {}, responses or {}
    async def generate_json(self, _system, payload, *, request_context, **_kwargs):
        ingredient_id = payload["ingredient_id"]; self.calls.append(payload)
        if ingredient_id in self.failures: raise self.failures[ingredient_id]
        ids = payload["communication_plan"]["primary"]["evidence_ids"]
        request_context["openrouter_diagnostics"] = {"attempt_count": 1, "resolved_model": "qwen/qwen3.5-9b", "resolved_provider": "fake", "raw_response_present": True, "content_present": True}
        return self.responses.get(ingredient_id, {"headline": "Cần theo dõi", "summary": "Có nguy cơ thiếu hàng trong kỳ kế hoạch.", "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": "Có nguy cơ thiếu hàng trong kỳ kế hoạch.", "evidence_ids": ids}], "used_evidence_ids": ids})


def test_routing_uses_riskdetail_not_stockout_date():
    n, w, c = brief(["n"]), brief(["w"]), brief(["c"], critical=("c",))
    ng, wg, cg = Gateway(), Gateway(), Gateway()
    nr = IngredientSynthesisProvider(ng, None).synthesize(n, facts(n))
    wr = IngredientSynthesisProvider(wg, None).synthesize(w, facts(w, ("w",)))
    cr = IngredientSynthesisProvider(cg, None).synthesize(c, facts(c, ("c",)))
    assert nr[0].importance == "normal" and nr[0].source == "rule_based" and not ng.calls
    assert wr[0].importance == "watch" and wr[0].source == "rule_based" and not wg.calls
    assert cr[0].importance == "critical" and cr[0].source == "llm" and len(cg.calls) == 1


def test_warning_riskdetail_is_watch_without_provider_call():
    value = brief(["w"], warning=("w",)); gateway = Gateway()
    result = IngredientSynthesisProvider(gateway, None).synthesize(value, facts(value))
    assert result[0].importance == "watch"
    assert result[0].source == "rule_based"
    assert gateway.calls == []


def test_per_item_calls_are_scoped_ordered_and_counted():
    value = brief(["a", "b", "c"], critical=("a", "b", "c")); gateway = Gateway(); provider = IngredientSynthesisProvider(gateway, None)
    result = provider.synthesize(value, facts(value, ("a", "b", "c")))
    assert [x.ingredient_id for x in result] == ["a", "b", "c"]
    assert [x.source for x in result] == ["llm", "llm", "llm"]
    assert len(gateway.calls) == provider.last_diagnostics["provider_call_count"] == 3
    assert provider.last_diagnostics["llm_success_count"] == 3
    assert all({r["ingredient_id"] for r in call["evidence"]} == {call["ingredient_id"]} for call in gateway.calls)


def test_token_limit_isolated_to_one_item():
    value = brief(["a", "b", "c"], critical=("a", "b", "c")); provider = IngredientSynthesisProvider(Gateway({"b": LLMProviderError("limit", details={"failure_stage": "TOKEN_LIMIT"})}), None)
    result = provider.synthesize(value, facts(value, ("a", "b", "c")))
    assert [x.source for x in result] == ["llm", "deterministic_fallback", "llm"]
    assert provider.last_diagnostics["status"] == "partial_success" and provider.last_diagnostics["llm_success_count"] == 2
    assert {x["ingredient_id"]: x for x in provider.last_diagnostics["items"]}["b"]["failure_stage"] == "TOKEN_LIMIT"


def test_network_failure_does_not_stop_siblings():
    value = brief(["a", "b", "c"], critical=("a", "b", "c")); provider = IngredientSynthesisProvider(Gateway({"b": LLMProviderError("network", details={"failure_stage": "NETWORK"})}), None)
    assert [x.source for x in provider.synthesize(value, facts(value, ("a", "b", "c")))] == ["llm", "deterministic_fallback", "llm"]


@pytest.mark.parametrize("stage", ["JSON_PARSE", "TIMEOUT"])
def test_provider_failure_stages_are_isolated(stage):
    value = brief(["a", "b", "c"], critical=("a", "b", "c"))
    provider = IngredientSynthesisProvider(Gateway({"b": LLMProviderError(stage, details={"failure_stage": stage})}), None)
    result = provider.synthesize(value, facts(value, ("a", "b", "c")))
    assert [item.source for item in result] == ["llm", "deterministic_fallback", "llm"]
    assert {item["ingredient_id"]: item for item in provider.last_diagnostics["items"]}["b"]["failure_stage"] == stage


def test_schema_failure_is_isolated():
    value = brief(["a", "b", "c"], critical=("a", "b", "c"))
    provider = IngredientSynthesisProvider(Gateway(responses={"b": {"headline": "missing required fields"}}), None)
    result = provider.synthesize(value, facts(value, ("a", "b", "c")))
    assert [item.source for item in result] == ["llm", "deterministic_fallback", "llm"]
    assert {item["ingredient_id"]: item for item in provider.last_diagnostics["items"]}["b"]["failure_stage"] == "SCHEMA_VALIDATION"


@pytest.mark.parametrize(("message", "stage"), [
    ("unsupported_numeric_claim", "NUMERIC_GROUNDING"),
    ("unsupported_causal_claim", "CAUSAL_GROUNDING"),
    ("ingredient_evidence_entity_mismatch", "ENTITY_GROUNDING"),
])
def test_grounding_failures_are_isolated(monkeypatch, message, stage):
    value = brief(["a", "b", "c"], critical=("a", "b", "c")); provider = IngredientSynthesisProvider(Gateway(), None)
    original = provider.guard._guard
    def guard(*args, target_ingredient_id=None, **kwargs):
        if target_ingredient_id == "b": raise ValueError(message)
        return original(*args, target_ingredient_id=target_ingredient_id, **kwargs)
    monkeypatch.setattr(provider.guard, "_guard", guard)
    result = provider.synthesize(value, facts(value, ("a", "b", "c")))
    assert [item.source for item in result] == ["llm", "deterministic_fallback", "llm"]
    assert {item["ingredient_id"]: item for item in provider.last_diagnostics["items"]}["b"]["failure_stage"] == stage


def test_run_diagnostics_aggregate_normal_watch_and_critical_items():
    value = brief(["n1", "n2", "w1", "w2", "w3", "c1", "c2"], critical=("c1", "c2"), warning=("w1",))
    provider = IngredientSynthesisProvider(Gateway(), None)
    provider.synthesize(value, facts(value, ("w2", "w3", "c1", "c2")))
    diagnostics = provider.last_diagnostics
    assert (diagnostics["total_ingredient_count"], diagnostics["normal_count"], diagnostics["watch_count"], diagnostics["critical_count"]) == (7, 2, 3, 2)
    assert (diagnostics["eligible_count"], diagnostics["provider_call_count"], diagnostics["llm_success_count"], diagnostics["fallback_count"]) == (2, 2, 2, 0)
    assert diagnostics["status"] == "success" and len(diagnostics["items"]) == 2


def test_representative_ten_ingredient_routing_uses_authoritative_critical_risk_only():
    ids = ["n1", "n2", "w1", "w2", "w3", "w4", "w5", "c1", "c2", "c3"]
    value = brief(ids, critical=("c1", "c2", "c3"), warning=("w1", "w2"))
    provider = IngredientSynthesisProvider(Gateway(), None)
    result = provider.synthesize(value, facts(value, ("w3", "w4", "w5", "c1", "c2", "c3")))
    by_id = {item.ingredient_id: item for item in result}
    assert [by_id[item].importance for item in ids] == ["normal", "normal", "watch", "watch", "watch", "watch", "watch", "critical", "critical", "critical"]
    assert all(by_id[item].source == "rule_based" for item in ("w3", "w4", "w5"))
    assert (provider.last_diagnostics["normal_count"], provider.last_diagnostics["watch_count"], provider.last_diagnostics["critical_count"], provider.last_diagnostics["eligible_count"], provider.last_diagnostics["provider_call_count"]) == (2, 5, 3, 3, 3)


def test_communication_plan_prioritizes_stockout_and_bounds_nonredundant_support():
    records = [
        {"evidence_id": "risk", "type": "INGREDIENT_OPERATIONAL_RISK", "first_stockout_date": "2026-08-21"},
        {"evidence_id": "order", "type": "PROCUREMENT_QUANTITY"},
        {"evidence_id": "alignment", "type": "DEMAND_ORDER_ALIGNMENT"},
        {"evidence_id": "demand", "type": "DEMAND_HORIZON_SUMMARY"},
    ]
    plan = IngredientSynthesisProvider._communication_plan(records)
    assert plan["primary"] == {"role": "stockout_timing", "evidence_ids": ["risk"]}
    assert [item["role"] for item in plan["supporting"]] == ["procurement_quantity", "procurement_alignment"]
    assert len(plan["supporting"]) == 2 and "demand" not in plan["authorized_evidence_ids"]
    assert plan["causal_allowed"] is False
    assert IngredientSynthesisProvider._classify_case(plan) == "STOCKOUT_BEFORE_RECEIPT"


def test_case_classifier_and_style_retrieval_are_deterministic_and_placeholder_only():
    material = {"primary": {"role": "shortage_risk", "evidence_ids": ["risk"]}, "supporting": [], "limitation": None, "causal_allowed": False, "authorized_evidence_ids": ["risk"]}
    limited = {"primary": {"role": "other_critical_operational_risk", "evidence_ids": ["risk"]}, "supporting": [], "limitation": {"role": "ingredient_limitation", "evidence_ids": ["limit"]}, "causal_allowed": False, "authorized_evidence_ids": ["risk", "limit"]}
    assert IngredientSynthesisProvider._classify_case(material) == "MATERIAL_SHORTAGE"
    assert IngredientSynthesisProvider._classify_case(limited) == "LIMITED_EVIDENCE"
    examples = retrieve_style_examples(task="ingredient_synthesis", intent="SYNTHESIS", case="STOCKOUT_BEFORE_RECEIPT", detail_level="simple", limit=2)
    assert examples == retrieve_style_examples(task="ingredient_synthesis", intent="SYNTHESIS", case="STOCKOUT_BEFORE_RECEIPT", detail_level="simple", limit=2)
    assert 1 <= len(examples) <= 2
    assert all("<" in item["template"] and "Ingredient a" not in item["template"] for item in examples)


def test_known_but_communication_plan_unauthorized_evidence_falls_back():
    value = brief(["a"], critical=("a",)); gateway = Gateway(); provider = IngredientSynthesisProvider(gateway, None)
    built_facts = facts(value, ("a",)); records, _ = provider._records(value, built_facts)
    risk = next(item["evidence_id"] for item in records if item["type"] == "INGREDIENT_OPERATIONAL_RISK")
    demand = next(item["evidence_id"] for item in records if item["type"] == "DEMAND_HORIZON_SUMMARY")
    provider._communication_plan = lambda _records: {"primary": {"role": "stockout_timing", "evidence_ids": [risk]}, "supporting": [], "limitation": None, "causal_allowed": False, "authorized_evidence_ids": [risk]}
    gateway.responses["a"] = {"headline": "Cần theo dõi", "summary": "Có nguy cơ thiếu hàng trong kỳ kế hoạch.", "claims": [{"type": "DEMAND_HORIZON_SUMMARY", "text": "Có nguy cơ thiếu hàng trong kỳ kế hoạch.", "evidence_ids": [demand]}], "used_evidence_ids": [demand]}
    result = provider.synthesize(value, built_facts)
    assert result[0].source == "deterministic_fallback"
    assert provider.last_diagnostics["items"][0]["failure_stage"] == "GROUNDING"


def test_quality_diagnostics_and_fallback_share_the_selected_primary_fact():
    value = brief(["a"], critical=("a",)); provider = IngredientSynthesisProvider(Gateway(), None)
    result = provider.synthesize(value, facts(value, ("a",)))
    item = provider.last_diagnostics["items"][0]
    assert result[0].source == "llm"
    assert item["communication_primary_role"] == "stockout_timing"
    assert item["case_archetype"] == "MATERIAL_SHORTAGE"
    assert item["selected_style_example_ids"]


@pytest.mark.parametrize("summary", [
    "Một. Hai. Ba. Bốn.",
    "Tỷ lệ lấp kho cần được theo dõi trong kỳ kế hoạch.",
])
def test_quality_guard_rejects_overlong_output_and_wrong_fill_rate_term(summary):
    value = brief(["a"], critical=("a",)); gateway = Gateway(); provider = IngredientSynthesisProvider(gateway, None)
    built_facts = facts(value, ("a",)); records, _ = provider._records(value, built_facts)
    risk = next(item["evidence_id"] for item in records if item["type"] == "INGREDIENT_OPERATIONAL_RISK")
    gateway.responses["a"] = {"headline": "Cần theo dõi", "summary": summary, "claims": [{"type": "INGREDIENT_OPERATIONAL_RISK", "text": summary, "evidence_ids": [risk]}], "used_evidence_ids": [risk]}
    assert provider.synthesize(value, built_facts)[0].source == "deterministic_fallback"
