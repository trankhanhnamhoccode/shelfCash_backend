"""Keep frontend-facing example payloads aligned with frozen Pydantic contracts."""
from __future__ import annotations

import json
import math
from pathlib import Path

from app.decision_intelligence.contracts import (
    DecisionBriefFacts,
    DecisionExplanationResponse,
    WhatIfResponse,
)
from app.schemas.decision import DecisionRunRequest, ExplanationRequest, WhatIfRequest


EXAMPLES = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "decision_assistant_frontend_examples.json"
)


def test_frontend_guide_exists_and_links_to_frozen_contract():
    guide = (Path(__file__).resolve().parents[1] / "docs" / "frontend_decision_assistant_integration.md").read_text(encoding="utf-8")
    assert "decision_assistant_api_contract.md" in guide
    assert "demand_change_percent" in guide
    assert "selected strategy" in guide and "candidate strategy" in guide
    assert "hypothetical" in guide and "baseline" in guide


def test_frontend_examples_validate_against_public_models():
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))

    DecisionRunRequest.model_validate(examples["create_decision_run_request"])
    DecisionBriefFacts.model_validate(examples["brief_response"])
    ExplanationRequest.model_validate(examples["general_explanation_request"])
    ExplanationRequest.model_validate(examples["ingredient_explanation_request"])
    DecisionExplanationResponse.model_validate(examples["explanation_response"])
    WhatIfRequest.model_validate(examples["what_if_demand_request"])
    WhatIfRequest.model_validate(examples["what_if_strategy_request"])
    WhatIfResponse.model_validate(examples["what_if_response"])


def test_frontend_horizon_examples_preserve_sum_daily_quantile_semantics():
    examples = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    briefs = [
        examples["brief_response"],
        examples["what_if_response"]["baseline"],
        examples["what_if_response"]["hypothetical"],
    ]
    for brief in briefs:
        summary = brief["ingredient_demand_summary"][0]
        daily = brief["ingredient_demand"]
        assert math.isclose(sum(row["p25"] for row in daily), summary["p25_total"])
        assert math.isclose(sum(row["p50"] for row in daily), summary["p50_total"])
        assert math.isclose(sum(row["p75"] for row in daily), summary["p75_total"])
        assert min(row["p50"] for row in daily) == summary["daily_p50_min"]
        assert max(row["p50"] for row in daily) == summary["daily_p50_max"]
