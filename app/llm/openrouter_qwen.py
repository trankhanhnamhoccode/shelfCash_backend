from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from json_repair import repair_json

from app.core.exceptions import LLMProviderError, LLMUnavailableError
from app.core.rule_mapper import finalize_mapping
from app.llm.base import LLMProvider
from app.schemas.llm import MappingSuggestion

logger = logging.getLogger("shelfcash.llm")


class OpenRouterQwenProvider(LLMProvider):
    def __init__(self, settings):
        self.settings = settings
        self.api_key = (getattr(settings, "openrouter_api_key", None) or "").strip()
        self.base_url = (getattr(settings, "openrouter_base_url", None) or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = getattr(settings, "openrouter_model", None) or "qwen/qwen3.5-9b"
        self.timeout = getattr(settings, "openrouter_timeout_seconds", 90)
        self.max_new_tokens = getattr(settings, "openrouter_max_new_tokens", 2000)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://shelfcash.local",
                    "X-Title": "ShelfCash",
                },
            )
        return self._client

    def health(self) -> dict[str, Any]:
        return {
            "provider": "openrouter_qwen",
            "model": self.model,
            "configured": bool(self.api_key),
            "available": self.available,
        }

    async def generate_json(
        self,
        system: str,
        payload: dict[str, Any],
        *,
        max_new_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise LLMUnavailableError("OpenRouter API key is not configured")

        started = time.monotonic()
        logger.info(
            "openrouter_request_started model=%s endpoint=%s/chat/completions",
            self.model, self.base_url,
        )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "max_tokens": max_new_tokens or self.max_new_tokens,
        }

        if response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}

        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"

        try:
            response = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_TIMEOUT duration_ms=%d",
                self.model, duration_ms,
            )
            raise LLMProviderError("OpenRouter request timed out", details={"reason": "OPENROUTER_TIMEOUT"}, http_status=504) from exc
        except httpx.RequestError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_NETWORK_ERROR duration_ms=%d error=%s",
                self.model, duration_ms, type(exc).__name__,
            )
            raise LLMProviderError("OpenRouter network connection error", details={"reason": "OPENROUTER_NETWORK_ERROR"}, http_status=502) from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        if response.status_code in (401, 403):
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_AUTH_FAILED status_code=%d duration_ms=%d",
                self.model, response.status_code, duration_ms,
            )
            raise LLMProviderError("OpenRouter authentication failed", details={"reason": "OPENROUTER_AUTH_FAILED", "status_code": response.status_code}, http_status=401)
        elif response.status_code == 402:
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_INSUFFICIENT_CREDITS status_code=402 duration_ms=%d",
                self.model, duration_ms,
            )
            raise LLMProviderError("OpenRouter insufficient credits", details={"reason": "OPENROUTER_INSUFFICIENT_CREDITS"}, http_status=402)
        elif response.status_code == 429:
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_RATE_LIMITED status_code=429 duration_ms=%d",
                self.model, duration_ms,
            )
            raise LLMProviderError("OpenRouter rate limit exceeded", details={"reason": "OPENROUTER_RATE_LIMITED"}, http_status=429)
        elif response.status_code >= 500:
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_UPSTREAM_ERROR status_code=%d duration_ms=%d",
                self.model, response.status_code, duration_ms,
            )
            raise LLMProviderError(f"OpenRouter upstream server error ({response.status_code})", details={"reason": "OPENROUTER_UPSTREAM_ERROR", "status_code": response.status_code}, http_status=502)
        elif response.is_error:
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_HTTP_ERROR status_code=%d duration_ms=%d",
                self.model, response.status_code, duration_ms,
            )
            raise LLMProviderError(f"OpenRouter HTTP error ({response.status_code})", details={"reason": "OPENROUTER_HTTP_ERROR", "status_code": response.status_code}, http_status=502)

        try:
            data = response.json()
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError("No choices in OpenRouter response")
            raw_text = choices[0].get("message", {}).get("content", "")
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise ValueError("Empty content in OpenRouter response")

            content = raw_text.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content
                content = content.rsplit("```", 1)[0].strip()

            parsed = json.loads(repair_json(content))
            if not isinstance(parsed, dict):
                raise ValueError("Parsed JSON must be an object")

            logger.info(
                "openrouter_request_completed model=%s duration_ms=%d",
                self.model, duration_ms,
            )
            return parsed
        except Exception as exc:
            logger.warning(
                "openrouter_request_failed model=%s reason=OPENROUTER_INVALID_RESPONSE duration_ms=%d error=%s",
                self.model, duration_ms, type(exc).__name__,
            )
            raise LLMProviderError("OpenRouter returned invalid JSON", details={"reason": "OPENROUTER_INVALID_RESPONSE"}, http_status=502) from exc

    async def map_sheet(self, profile, canonical_schemas: dict, rule_suggestion: MappingSuggestion) -> MappingSuggestion:
        system = (
            "You map Excel sheet profiles to canonical schemas. Return exactly one JSON object, "
            "with sheet_type, confidence, column_mapping, warnings, errors. Map every source column "
            "to a valid schema field or null. Never add source columns."
        )
        user_payload = {
            "profile": profile.model_dump(mode="json"),
            "canonical_schemas": canonical_schemas,
            "rule_suggestion": rule_suggestion.model_dump(),
        }

        threshold = getattr(self.settings, "rule_confidence_threshold", 0.82)

        if not self.available:
            # Check if rule suggestion is safe
            if rule_suggestion.confidence >= threshold or (rule_suggestion.sheet_type != "unknown" and rule_suggestion.column_mapping):
                fallback = rule_suggestion.model_copy(deep=True)
                fallback.source = "rule_fallback"
                fallback.requires_review = True
                return fallback
            raise LLMUnavailableError()

        try:
            raw = await self.generate_json(system, user_payload)
            return self._validate_mapping_result(raw, profile)
        except Exception as exc:
            logger.warning(
                "openrouter_mapping_failed reason=%s fallback_to_rule=True",
                type(exc).__name__,
            )
            if rule_suggestion.confidence >= threshold or (rule_suggestion.sheet_type != "unknown" and rule_suggestion.column_mapping):
                fallback = rule_suggestion.model_copy(deep=True)
                fallback.source = "rule_fallback"
                fallback.requires_review = True
                fallback.warnings.append("LLM mapping failed; rule suggestion retained")
                return fallback
            raise LLMUnavailableError() from exc

    def _validate_mapping_result(self, raw: dict[str, Any], profile) -> MappingSuggestion:
        columns = set(profile.columns)
        mapping = raw.get("column_mapping", {})
        if not isinstance(mapping, dict) or set(mapping) != columns:
            raise ValueError("LLM must map every and only source column")
        raw["source"] = "llm"
        raw["requires_review"] = False
        suggestion = MappingSuggestion.model_validate(raw)
        threshold = getattr(self.settings, "rule_confidence_threshold", 0.82)
        return finalize_mapping(profile, suggestion, threshold)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
