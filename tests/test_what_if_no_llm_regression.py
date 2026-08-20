import ast
from pathlib import Path


def test_what_if_computation_remains_deterministic_before_optional_narration():
    """M1-M5 recomputation stays local; narration receives only completed facts."""
    path = Path(__file__).parents[1] / "app" / "services" / "decision_planning_service.py"
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    service = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DecisionPlanningService")
    method = next(node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == "what_if_decision")
    method_source = ast.get_source_segment(source, method) or ""

    assert "generate_json" not in method_source
    assert "OpenRouter" not in method_source
    assert "DecisionNarrativeProvider" not in method_source
    assert "adapter.optimize" in method_source
    assert "build_what_if_facts" in method_source
    assert "WhatIfNarrativeProvider" in method_source
