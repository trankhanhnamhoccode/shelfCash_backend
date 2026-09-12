from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.decision_intelligence.what_if_evidence import WhatIfEvidenceFact, _validate_numbers
from app.services.decision.explain_decision import ExplainDecision


def test_legacy_template_fallback_keeps_its_current_generic_response():
    reader = SimpleNamespace(read=lambda _run_id: {
        "reason_codes": [{"code": "PACK_SIZE_ROUNDING"}],
        "warnings": ["current warning"],
    })
    service = ExplainDecision(None, reader, None, None)

    response = service._template_explanation(
        "run-1", SimpleNamespace(language="vi", detail_level="simple"),
    )

    assert response["source"] == "template"
    assert response["provider"] == "legacy_template_fallback"
    assert response["summary"] == (
        "Kế hoạch dựa trên forecast, BOM, tồn kho theo lô và các ràng buộc nhà cung cấp hiện có."
    )
    assert response["why_this_plan"] == [response["summary"]]
    assert response["main_risks"] == ["current warning"]


def test_what_if_numeric_guard_keeps_only_authorized_display_mentions():
    item = WhatIfEvidenceFact(
        evidence_id="fact-1",
        fact_type="WHAT_IF_MUTATION",
        values={"allowed_numeric_mentions": ["20%", "1.200.000"]},
    )

    _validate_numbers("Nhu cầu tăng 20% và chi phí tăng 1.200.000.", [item])
    with pytest.raises(ValueError, match="unsupported_numeric_claim"):
        _validate_numbers("Nhu cầu tăng 21%.", [item])
