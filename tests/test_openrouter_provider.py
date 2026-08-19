import json
import pytest
from datetime import date, datetime, timezone
import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.exceptions import LLMProviderError, LLMUnavailableError
from app.decision_intelligence.contracts import (
    CriticBrief, DecisionBriefFacts, ForecastBrief, IngredientDemandBrief,
    ProcurementRowBrief, RecommendationBrief, RiskBrief,
)
from app.decision_intelligence.narrative import DecisionNarrativeProvider
from app.llm.openrouter_qwen import OpenRouterQwenProvider
from app.main import create_app
from app.schemas.llm import SheetProfile, MappingSuggestion
from tests.conftest import migrate_database


def test_provider_health_configured():
    settings = Settings(
        openrouter_api_key="sk-or-v1-mock-test-key",
        openrouter_model="qwen/qwen3.5-9b",
    )
    provider = OpenRouterQwenProvider(settings)
    health = provider.health()
    assert health == {
        "provider": "openrouter_qwen",
        "model": "qwen/qwen3.5-9b",
        "configured": True,
        "available": True,
    }
    assert "sk-or-v1-mock-test-key" not in str(health)


def test_provider_health_unconfigured():
    settings = Settings(openrouter_api_key="")
    provider = OpenRouterQwenProvider(settings)
    health = provider.health()
    assert health == {
        "provider": "openrouter_qwen",
        "model": "qwen/qwen3.5-9b",
        "configured": False,
        "available": False,
    }
    assert provider.available is False


@pytest.mark.asyncio
async def test_generate_json_success(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    expected_payload = {"answer": "Kế hoạch ghi nhận đặt 60 lít Sữa tươi.", "claims": []}

    async def mock_post(url, json, **kwargs):
        assert "chat/completions" in url
        assert json["model"] == "qwen/qwen3.5-9b"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": f"```json\n{json_dumps(expected_payload)}\n```"
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    def json_dumps(obj):
        return json.dumps(obj)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    result = await provider.generate_json("system instruction", {"test": 123})
    assert result == expected_payload
    await provider.close()


@pytest.mark.asyncio
async def test_generate_json_auth_failure(monkeypatch):
    settings = Settings(openrouter_api_key="invalid-key")
    provider = OpenRouterQwenProvider(settings)

    async def mock_post(url, json, **kwargs):
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API key"}},
            request=httpx.Request("POST", url),
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate_json("system", {})
    assert exc_info.value.details["reason"] == "OPENROUTER_AUTH_FAILED"
    assert exc_info.value.http_status == 401
    await provider.close()


@pytest.mark.asyncio
async def test_generate_json_insufficient_credits(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    async def mock_post(url, json, **kwargs):
        return httpx.Response(
            402,
            json={"error": {"message": "Insufficient credits"}},
            request=httpx.Request("POST", url),
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate_json("system", {})
    assert exc_info.value.details["reason"] == "OPENROUTER_INSUFFICIENT_CREDITS"
    await provider.close()


@pytest.mark.asyncio
async def test_generate_json_rate_limit(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    async def mock_post(url, json, **kwargs):
        return httpx.Response(
            429,
            json={"error": {"message": "Rate limit exceeded"}},
            request=httpx.Request("POST", url),
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate_json("system", {})
    assert exc_info.value.details["reason"] == "OPENROUTER_RATE_LIMITED"
    await provider.close()


@pytest.mark.asyncio
async def test_generate_json_timeout(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    async def mock_post(url, json, **kwargs):
        raise httpx.TimeoutException("Read timed out")

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate_json("system", {})
    assert exc_info.value.details["reason"] == "OPENROUTER_TIMEOUT"
    await provider.close()


@pytest.mark.asyncio
async def test_map_sheet_success(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    profile = SheetProfile(
        file_name="sales.csv", sheet_name="sales", header_row_zero_based=0,
        row_count=1, column_count=3, columns=["Ngày", "Tên món", "SL bán"],
        dtypes={}, sample_rows=[],
    )
    rule_suggestion = MappingSuggestion(
        sheet_type="sales_history", confidence=0.7,
        column_mapping={"Ngày": "date", "Tên món": "product_name", "SL bán": "quantity_sold"},
        warnings=[], errors=[], source="rule", requires_review=True,
    )

    async def mock_post(url, json, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"sheet_type": "sales_history", "confidence": 0.95, "column_mapping": {"Ngày": "date", "Tên món": "product_name", "SL bán": "quantity_sold"}, "warnings": [], "errors": []}'
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    result = await provider.map_sheet(profile, {}, rule_suggestion)
    assert result.sheet_type == "sales_history"
    assert result.source == "llm"
    assert result.confidence == 0.95
    assert not result.requires_review
    await provider.close()


@pytest.mark.asyncio
async def test_map_sheet_openrouter_failure_with_safe_rule_fallback(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    profile = SheetProfile(
        file_name="sales.csv", sheet_name="sales", header_row_zero_based=0,
        row_count=1, column_count=3, columns=["Ngày", "Tên món", "SL bán"],
        dtypes={}, sample_rows=[],
    )
    rule_suggestion = MappingSuggestion(
        sheet_type="sales_history", confidence=0.85,
        column_mapping={"Ngày": "date", "Tên món": "product_name", "SL bán": "quantity_sold"},
        warnings=[], errors=[], source="rule", requires_review=False,
    )

    async def mock_post(url, json, **kwargs):
        return httpx.Response(500, request=httpx.Request("POST", url))

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    result = await provider.map_sheet(profile, {}, rule_suggestion)
    assert result.source == "rule_fallback"
    assert result.requires_review is True
    assert "LLM mapping failed; rule suggestion retained" in result.warnings
    await provider.close()


@pytest.mark.asyncio
async def test_map_sheet_openrouter_failure_with_no_safe_rule_raises_llm_unavailable(monkeypatch):
    settings = Settings(openrouter_api_key="mock-key")
    provider = OpenRouterQwenProvider(settings)

    profile = SheetProfile(
        file_name="unknown.csv", sheet_name="Sheet1", header_row_zero_based=0,
        row_count=1, column_count=1, columns=["Col1"],
        dtypes={}, sample_rows=[],
    )
    rule_suggestion = MappingSuggestion(
        sheet_type="unknown", confidence=0.0,
        column_mapping={"Col1": None},
        warnings=[], errors=[], source="rule", requires_review=True,
    )

    async def mock_post(url, json, **kwargs):
        return httpx.Response(500, request=httpx.Request("POST", url))

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", mock_post)

    with pytest.raises(LLMUnavailableError):
        await provider.map_sheet(profile, {}, rule_suggestion)
    await provider.close()


def test_api_llm_health_endpoint(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    migrate_database(db_url)
    settings = Settings(
        database_url=db_url,
        openrouter_api_key="test-key-123",
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
        forecast_artifact_root=tmp_path / "forecast_artifacts",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        res = client.get("/api/v1/llm/health")
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "openrouter_qwen"
        assert data["model"] == "qwen/qwen3.5-9b"
        assert data["configured"] is True
        assert data["available"] is True
        assert "test-key-123" not in str(data)


def test_api_map_sheet_when_llm_unconfigured_uses_safe_rule(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    migrate_database(db_url)
    settings = Settings(
        database_url=db_url,
        openrouter_api_key="",
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
        forecast_artifact_root=tmp_path / "forecast_artifacts",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/llm/map-sheet",
            json={
                "profile": {
                    "file_name": "sales.csv",
                    "sheet_name": "sales",
                    "header_row_zero_based": 0,
                    "row_count": 1,
                    "column_count": 3,
                    "columns": ["Ngày", "Tên món", "SL bán"],
                    "dtypes": {},
                    "sample_rows": [],
                }
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["sheet_type"] == "sales_history"
        assert data["source"] == "rule_fallback"
        assert data["requires_review"] is True


def test_api_map_sheet_when_llm_unconfigured_and_unknown_rule_returns_503(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    migrate_database(db_url)
    settings = Settings(
        database_url=db_url,
        openrouter_api_key="",
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
        forecast_artifact_root=tmp_path / "forecast_artifacts",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/llm/map-sheet",
            json={
                "profile": {
                    "file_name": "mystery.csv",
                    "sheet_name": "mystery",
                    "header_row_zero_based": 0,
                    "row_count": 1,
                    "column_count": 2,
                    "columns": ["foo_random", "bar_random"],
                    "dtypes": {},
                    "sample_rows": [],
                }
            },
        )
        assert res.status_code == 503
        data = res.json()
        assert data["code"] == "LLM_UNAVAILABLE"


def test_decision_narrative_openrouter_grounded_success():
    demand = [IngredientDemandBrief(ingredient_id="milk", ingredient_name="Sữa tươi", target_date=date(2026, 8, 20), unit="lít", p25=1, p50=2, p75=3, contributions=[])]
    brief = DecisionBriefFacts(
        decision_run_id="narrative-openrouter-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(forecast_run_id="forecast", horizon_days=1, cutoff_date=date(2026, 8, 19)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="milk", ingredient_name="Sữa tươi", supplier_id="supplier", quantity=60, unit="lít", reason_codes=[])],
        ingredient_demand=demand, risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )

    class MockOpenRouterLLM:
        available = True

        async def generate_json(self, system, payload, **kwargs):
            order = next(item for item in payload["evidence"] if item["type"] == "PROCUREMENT_QUANTITY")
            return {
                "answer": "Kế hoạch ghi nhận đặt 60 lít Sữa tươi.",
                "claims": [{"type": "PROCUREMENT_QUANTITY", "text": "Kế hoạch ghi nhận đặt 60 lít Sữa tươi.", "evidence_ids": [order["evidence_id"]]}],
                "used_evidence_ids": [order["evidence_id"]],
            }

    settings = Settings(openrouter_api_key="mock-key")
    result = DecisionNarrativeProvider(MockOpenRouterLLM(), settings).explain(brief, question="Tại sao phải nhập Sữa tươi?", language="vi", detail_level="simple")
    assert result.provider == "openrouter_qwen"
    assert result.source == "openrouter_qwen"
    assert result.grounded is True
    assert len(result.claims) == 1
    assert result.claims[0].value == "Kế hoạch ghi nhận đặt 60 lít Sữa tươi."
    assert result.raw_response is not None
    assert result.raw_response["answer"] == "Kế hoạch ghi nhận đặt 60 lít Sữa tươi."


def test_decision_narrative_openrouter_failure_falls_back_deterministically():
    demand = [IngredientDemandBrief(ingredient_id="milk", ingredient_name="Sữa tươi", target_date=date(2026, 8, 20), unit="lít", p25=1, p50=2, p75=3, contributions=[])]
    brief = DecisionBriefFacts(
        decision_run_id="narrative-openrouter-run", store_id="STORE_001", status="completed",
        forecast=ForecastBrief(forecast_run_id="forecast", horizon_days=1, cutoff_date=date(2026, 8, 19)),
        recommendation=RecommendationBrief(available=True, strategy="balanced"),
        procurement_rows=[ProcurementRowBrief(ingredient_id="milk", ingredient_name="Sữa tươi", supplier_id="supplier", quantity=60, unit="lít", reason_codes=[])],
        ingredient_demand=demand, risk=RiskBrief(), critic=CriticBrief(), generated_at=datetime.now(timezone.utc),
    )

    class FailingLLM:
        available = True

        async def generate_json(self, system, payload, **kwargs):
            raise LLMProviderError("OpenRouter 502 Bad Gateway")

    settings = Settings(openrouter_api_key="mock-key")
    result = DecisionNarrativeProvider(FailingLLM(), settings).explain(brief, question="Tại sao phải nhập Sữa tươi?", language="vi", detail_level="simple")
    assert result.provider == "deterministic_fallback"
    assert result.grounded is True
