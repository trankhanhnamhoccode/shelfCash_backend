"""Phase-7 contract freeze checks; intentionally not business-logic tests."""
from app.config import Settings
from app.main import create_app


BASE = "/api/v1"


def _openapi():
    return create_app(Settings()).openapi()


def test_decision_assistant_routes_and_openapi_response_models_are_frozen():
    document = _openapi()
    paths = document["paths"]
    expected = {
        f"{BASE}/stores/{{store_id}}/decision-runs": "post",
        f"{BASE}/decision-runs/{{decision_run_id}}": "get",
        f"{BASE}/decision-runs/{{decision_run_id}}/brief": "get",
        f"{BASE}/decision-runs/{{decision_run_id}}/explanation": "post",
        f"{BASE}/decision-runs/{{decision_run_id}}/what-if": "post",
    }
    assert {path: method for path, method in expected.items() if method in paths.get(path, {})} == expected
    assert not any(path.startswith(f"{BASE}/v2/") or "/v2/" in path for path in paths)

    raw_create = paths[f"{BASE}/stores/{{store_id}}/decision-runs"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    raw_read = paths[f"{BASE}/decision-runs/{{decision_run_id}}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    # Route-specific generated titles differ; object typing is the meaningful contract.
    assert raw_create["type"] == raw_read["type"] == "object"
    assert raw_create["additionalProperties"] is raw_read["additionalProperties"] is True

    responses = {
        "brief": "DecisionBriefFacts",
        "explanation": "DecisionExplanationResponse",
        "what-if": "WhatIfResponse",
    }
    for suffix, component in responses.items():
        operation = paths[f"{BASE}/decision-runs/{{decision_run_id}}/{suffix}"]["get" if suffix == "brief" else "post"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{component}"}


def test_brief_contract_required_fields_nullability_and_enums_are_frozen():
    schemas = _openapi()["components"]["schemas"]
    brief = schemas["DecisionBriefFacts"]
    required = set(brief["required"])
    assert required == {
        "decision_run_id", "store_id", "status", "forecast", "recommendation",
        "risk", "critic", "generated_at",
    }
    assert {
        "procurement_rows", "ingredient_demand", "ingredient_demand_summary", "risk_details",
        "evidence", "data_availability", "assistant_summary", "strategy_comparison",
    } <= set(brief["properties"])
    assert brief["properties"]["assistant_summary"]["anyOf"][1] == {"type": "null"}
    assert brief["properties"]["strategy_comparison"]["anyOf"][1] == {"type": "null"}
    assert schemas["IngredientDemandSummaryBrief"]["properties"]["aggregation_method"]["const"] == "sum_daily_quantiles"
    assert schemas["AssistantSummary"]["properties"]["source"]["enum"] == ["llm", "deterministic_fallback"]
    assert schemas["RiskDetail"]["properties"]["classification"]["enum"] == ["risk", "limitation", "unknown"]
    assert schemas["RiskDetail"]["properties"]["severity"]["enum"] == ["info", "warning", "critical"]


def test_explanation_and_what_if_contracts_preserve_targeting_and_delta_semantics():
    schemas = _openapi()["components"]["schemas"]
    request = schemas["ExplanationRequest"]
    assert request["properties"]["language"]["enum"] == ["vi", "en"]
    assert request["properties"]["detail_level"]["enum"] == ["simple", "manager", "technical"]
    assert request["properties"]["ingredient_id"]["anyOf"][1] == {"type": "null"}

    what_if_request = schemas["WhatIfRequest"]["properties"]
    assert what_if_request["demand_multiplier"]["anyOf"][0]["exclusiveMinimum"] == 0.0
    assert what_if_request["supplier_delay_days"]["anyOf"][0]["minimum"] == 0.0
    assert what_if_request["strategy"]["anyOf"][0]["enum"] == ["lean", "balanced", "protected"]

    mutation = schemas["WhatIfMutationFacts"]["properties"]
    assert "demand_change_percent" in mutation
    assert mutation["demand_change_percentage_points"]["deprecated"] is True
    comparison = schemas["WhatIfComparison"]["properties"]
    assert {"new_issues", "resolved_issues", "new_risks", "resolved_risks", "order_changes", "strategy_change"} <= set(comparison)
    assert comparison["new_risks"]["deprecated"] is True
    assert comparison["resolved_risks"]["deprecated"] is True
    assert schemas["WhatIfOrderChange"]["properties"]["change_type"]["anyOf"][0]["enum"] == ["added", "removed", "increased", "decreased"]
    assert schemas["WhatIfResponse"]["properties"]["mutations"] == {"$ref": "#/components/schemas/WhatIfRequest"}
    assert schemas["WhatIfResponse"]["properties"]["grounded_explanation"]["anyOf"][0] == {"$ref": "#/components/schemas/DecisionExplanationResponse"}


def test_contract_errors_use_the_standard_envelope(client):
    response = client.post(f"{BASE}/decision-runs/unknown/what-if", json={"demand_multiplier": -1})
    assert response.status_code == 422
    body = response.json()
    assert {"code", "message", "details", "request_id"} <= set(body)
    assert body["code"] == "validation_error"
