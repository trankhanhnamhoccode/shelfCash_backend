from __future__ import annotations

import asyncio

from app.config import Settings
from app.core.ingestion_pipeline import IngestionPipeline
from app.core.rule_mapper import map_sheet_rules
from app.schemas.llm import MappingSuggestion, SheetProfile


HIGH_CONFIDENCE_PROFILE = {
    "file_name": "sales.csv",
    "sheet_name": "sales",
    "header_row_zero_based": 0,
    "row_count": 1,
    "column_count": 3,
    "columns": ["ngay", "ten mon", "sl ban"],
    "dtypes": {},
    "sample_rows": [],
}

AMBIGUOUS_PROFILE = {
    "file_name": "partial-sales.csv",
    "sheet_name": "sales",
    "header_row_zero_based": 0,
    "row_count": 1,
    "column_count": 3,
    "columns": ["ngay", "unknown_product", "unknown_quantity"],
    "dtypes": {},
    "sample_rows": [],
}


class SpyMappingProvider:
    def __init__(self, *, available=True, result=None, error=None):
        self.available = available
        self.result = result
        self.error = error
        self.calls = []

    async def map_sheet(self, profile, canonical_schemas, rule_suggestion):
        self.calls.append((profile, canonical_schemas, rule_suggestion))
        if self.error:
            raise self.error
        return self.result


def _post(client, profile):
    return client.post("/api/v1/llm/map-sheet", json={"profile": profile})


def test_high_confidence_api_and_pipeline_return_canonical_rule_without_provider(client):
    profile = SheetProfile.model_validate(HIGH_CONFIDENCE_PROFILE)
    threshold = Settings(_env_file=None).rule_confidence_threshold
    expected = map_sheet_rules(profile, threshold)
    assert expected.confidence >= threshold
    assert expected.source == "rule"
    assert expected.requires_review is False
    assert expected.raw_response is None

    provider = SpyMappingProvider(available=True)
    client.app.state.llm_provider = provider
    response = _post(client, HIGH_CONFIDENCE_PROFILE)

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert provider.calls == []

    unavailable_provider = SpyMappingProvider(available=False)
    client.app.state.llm_provider = unavailable_provider
    unavailable_response = _post(client, HIGH_CONFIDENCE_PROFILE)
    assert unavailable_response.status_code == 200
    assert unavailable_response.json() == expected.model_dump(mode="json")
    assert unavailable_provider.calls == []

    pipeline_provider = SpyMappingProvider(available=True)
    pipeline_result = asyncio.run(IngestionPipeline(pipeline_provider, threshold).suggest(profile))
    assert pipeline_result.model_dump(mode="json") == expected.model_dump(mode="json")
    assert pipeline_provider.calls == []


def test_ambiguous_api_and_pipeline_escalate_to_mapping_provider(client):
    profile = SheetProfile.model_validate(AMBIGUOUS_PROFILE)
    threshold = Settings(_env_file=None).rule_confidence_threshold
    rule = map_sheet_rules(profile, threshold)
    assert rule.confidence < threshold

    provider_result = MappingSuggestion(
        sheet_type="sales_history",
        confidence=0.95,
        column_mapping={
            "ngay": "date",
            "unknown_product": "product_name",
            "unknown_quantity": "quantity_sold",
        },
        source="llm",
        requires_review=False,
        raw_response={"task": "excel_mapping"},
    )
    api_provider = SpyMappingProvider(result=provider_result)
    client.app.state.llm_provider = api_provider
    response = _post(client, AMBIGUOUS_PROFILE)

    assert response.status_code == 200
    assert response.json() == provider_result.model_dump(mode="json")
    assert len(api_provider.calls) == 1
    assert api_provider.calls[0][2].model_dump(mode="json") == rule.model_dump(mode="json")

    pipeline_provider = SpyMappingProvider(result=provider_result)
    pipeline_result = asyncio.run(IngestionPipeline(pipeline_provider, threshold).suggest(profile))
    assert len(pipeline_provider.calls) == 1
    assert pipeline_provider.calls[0][2].model_dump(mode="json") == rule.model_dump(mode="json")
    assert pipeline_result.source == "llm"
    assert pipeline_result.requires_review is False
    assert pipeline_result.raw_response == {"task": "excel_mapping"}


def test_ambiguous_provider_failure_keeps_direct_api_safe_rule_fallback(client):
    profile = SheetProfile.model_validate(AMBIGUOUS_PROFILE)
    threshold = Settings(_env_file=None).rule_confidence_threshold
    rule = map_sheet_rules(profile, threshold)
    assert rule.confidence < threshold
    assert rule.sheet_type == "sales_history"
    assert rule.column_mapping["ngay"] == "date"

    provider = SpyMappingProvider(error=RuntimeError("provider failed"))
    client.app.state.llm_provider = provider
    response = _post(client, AMBIGUOUS_PROFILE)

    assert response.status_code == 200
    assert len(provider.calls) == 1
    assert response.json()["source"] == "rule_fallback"
    assert response.json()["requires_review"] is True
    assert response.json()["raw_response"] == {
        "failure_stage": "UNKNOWN",
        "reason": "RuntimeError",
    }
    assert any("LLM mapping failed (UNKNOWN)" in warning for warning in response.json()["warnings"])
