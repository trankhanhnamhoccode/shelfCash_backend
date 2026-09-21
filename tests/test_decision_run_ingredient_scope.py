from app.services.decision.decision_run_ingredient_scope import decision_run_ingredient_ids


def test_persisted_decision_run_scope_is_demand_and_plan_union_only():
    package = {
        "ingredient_demand": [
            {"ingredient_id": "banana"},
            {"ingredient_id": "banana"},
        ],
        "recommended_plan": {"items": [
            {"ingredient_id": "banana"},
            {"ingredient_id": "mango"},
        ]},
        "inventory_risk": {"ingredient_id": "risk-only"},
        "assistant": {"ingredient_synthesis": [{"ingredient_id": "synthesis-only"}]},
    }

    assert decision_run_ingredient_ids(package) == {"banana", "mango"}


def test_persisted_decision_run_scope_tolerates_missing_or_non_list_sections():
    assert decision_run_ingredient_ids({"ingredient_demand": {}, "recommended_plan": []}) == set()
