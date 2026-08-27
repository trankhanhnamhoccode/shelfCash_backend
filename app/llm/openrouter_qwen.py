from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import LLMProviderError, LLMUnavailableError
from app.core.logging_context import get_request_id
from app.core.rule_mapper import finalize_mapping
from app.decision_intelligence.contracts import (
    DecisionNarrativeLLMResponse,
    DecisionOverallSummaryLLMResponse,
)
from app.llm.base import LLMProvider
from app.llm.tasks import LLMFailureStage, LLMTask, OpenRouterTaskProfile
from app.schemas.llm import MappingSuggestion

logger = logging.getLogger("shelfcash.llm")


class OpenRouterLLMGateway(LLMProvider):
    """Task-aware OpenRouter gateway with no model-specific business API."""

    def __init__(self, settings):
        self.settings = settings
        self.api_key = (getattr(settings, "openrouter_api_key", None) or "").strip()
        self.base_url = (getattr(settings, "openrouter_base_url", None) or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = getattr(settings, "openrouter_model", None) or "qwen/qwen3.5-9b"
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(getattr(self.settings, "openrouter_timeout_seconds", 60), connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://shelfcash.local",
                    "X-Title": "ShelfCash",
                    # OpenRouter only returns resolved routing details when this
                    # opt-in header is present; it contains no prompt content.
                    "X-OpenRouter-Metadata": "enabled",
                },
            )
        return self._client

    def health(self) -> dict[str, Any]:
        # Keep the existing public health response stable.
        return {
            "provider": "openrouter_qwen",
            "model": self.model,
            "configured": bool(self.api_key),
            "available": self.available,
        }

    def task_profile(self, task: LLMTask) -> OpenRouterTaskProfile:
        if task not in (LLMTask.EXCEL_MAPPING, LLMTask.DECISION_NARRATIVE, LLMTask.PLAN_SUMMARY):
            raise ValueError(f"Unsupported LLM task: {task}")
        prefix = {
            LLMTask.EXCEL_MAPPING: "openrouter_mapping",
            LLMTask.DECISION_NARRATIVE: "openrouter_narrative",
            LLMTask.PLAN_SUMMARY: "openrouter_summary",
        }[task]
        # Task models own routing.  Do not let a legacy OPENROUTER_MODEL value
        # silently switch either current production task to another model.
        configured_model = getattr(self.settings, f"{prefix}_model", None) or "qwen/qwen3.5-9b"
        return OpenRouterTaskProfile(
            model=configured_model,
            temperature=float(getattr(self.settings, f"{prefix}_temperature", 0.0)),
            max_tokens=int(getattr(self.settings, f"{prefix}_max_tokens", 1800 if task is LLMTask.EXCEL_MAPPING else 900 if task is LLMTask.PLAN_SUMMARY else 1200)),
            timeout_seconds=float(getattr(self.settings, f"{prefix}_timeout_seconds", 60)),
            reasoning_enabled=bool(getattr(self.settings, f"{prefix}_reasoning_enabled", False)),
            structured_output=bool(getattr(self.settings, f"{prefix}_structured_output", True)),
            strict_schema=bool(getattr(self.settings, f"{prefix}_strict_schema", True)),
            require_parameters=bool(getattr(self.settings, f"{prefix}_require_parameters", True)),
        )

    @staticmethod
    def _response_schema(task: LLMTask) -> dict[str, Any]:
        if task is LLMTask.EXCEL_MAPPING:
            return MappingSuggestion.model_json_schema()
        if task is LLMTask.DECISION_NARRATIVE:
            return DecisionNarrativeLLMResponse.model_json_schema()
        if task is LLMTask.PLAN_SUMMARY:
            return DecisionOverallSummaryLLMResponse.model_json_schema()
        raise ValueError(f"Unsupported LLM task: {task}")

    @staticmethod
    def _retry_delay(response: httpx.Response | None = None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            try:
                return min(max(float(retry_after), 0.05), 1.0)
            except (TypeError, ValueError):
                pass
        return 0.25

    @staticmethod
    def _value(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("name", "id", "slug"):
                if isinstance(value.get(key), str):
                    return value[key]
        return None

    def _metadata(self, data: dict[str, Any], choice: dict[str, Any] | None = None) -> dict[str, Any]:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        completion_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        router_metadata = data.get("openrouter_metadata") if isinstance(data.get("openrouter_metadata"), dict) else {}
        endpoints = router_metadata.get("endpoints") if isinstance(router_metadata.get("endpoints"), dict) else {}
        available_endpoints = endpoints.get("available") if isinstance(endpoints.get("available"), list) else []
        selected_endpoint = next(
            (endpoint for endpoint in available_endpoints if isinstance(endpoint, dict) and endpoint.get("selected") is True),
            {},
        )
        return {
            "resolved_model": self._value(selected_endpoint.get("model")) or self._value(data.get("model")),
            "resolved_provider": self._value(selected_endpoint.get("provider")) or self._value(data.get("provider")),
            "routing_strategy": self._value(router_metadata.get("strategy")),
            "routing_attempt": router_metadata.get("attempt"),
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "native_finish_reason": choice.get("native_finish_reason") if isinstance(choice, dict) else None,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": completion_details.get("reasoning_tokens"),
            "cost": usage.get("cost"),
        }

    @staticmethod
    def _failure_stage(exc: Exception) -> str:
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details.get("failure_stage"):
            return str(details["failure_stage"])
        return LLMFailureStage.UNKNOWN.value

    @staticmethod
    def _is_token_limit(metadata: dict[str, Any]) -> bool:
        """Recognize provider truncation before attempting to parse partial JSON."""
        reasons = {
            str(metadata.get(key) or "").strip().lower()
            for key in ("finish_reason", "native_finish_reason")
        }
        return bool(reasons & {"length", "max_tokens", "max_token", "token_limit"})

    def _provider_error(
        self,
        message: str,
        *,
        stage: LLMFailureStage,
        task: LLMTask,
        profile: OpenRouterTaskProfile,
        http_status: int = 502,
        metadata: dict[str, Any] | None = None,
    ) -> LLMProviderError:
        details: dict[str, Any] = {
            "reason": stage.value,
            "failure_stage": stage.value,
            "task": task.value,
            "configured_model": profile.model,
        }
        if metadata:
            details.update({key: value for key, value in metadata.items() if value is not None})
        return LLMProviderError(message, details=details, http_status=http_status)

    async def generate_json(
        self,
        system: str,
        payload: dict[str, Any],
        *,
        task: LLMTask = LLMTask.EXCEL_MAPPING,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise LLMUnavailableError("OpenRouter API key is not configured")

        profile = self.task_profile(task)
        request_id = get_request_id()
        decision_run_id = (request_context or {}).get("decision_run_id")
        body: dict[str, Any] = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "reasoning": {"enabled": True} if profile.reasoning_enabled else {"effort": "none"},
            "provider": {"require_parameters": profile.require_parameters},
        }
        if profile.structured_output:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": task.value,
                    "strict": profile.strict_schema,
                    "schema": self._response_schema(task),
                },
            }

        started = time.monotonic()
        logger.info(
            "openrouter_request_started request_id=%s decision_run_id=%s task=%s configured_model=%s timeout_seconds=%s max_tokens=%s reasoning=%s structured_output=%s strict_schema=%s require_parameters=%s",
            request_id, decision_run_id, task.value, profile.model, profile.timeout_seconds,
            profile.max_tokens, "enabled" if profile.reasoning_enabled else "off",
            profile.structured_output, profile.strict_schema, profile.require_parameters,
        )
        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                response = await client.post(
                    url,
                    json=body,
                    timeout=httpx.Timeout(profile.timeout_seconds, connect=min(10.0, profile.timeout_seconds)),
                )
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    logger.warning(
                        "openrouter_request_retry request_id=%s decision_run_id=%s task=%s configured_model=%s failure_stage=%s attempt=%s",
                        request_id, decision_run_id, task.value, profile.model, LLMFailureStage.TIMEOUT.value, attempt + 1,
                    )
                    await asyncio.sleep(self._retry_delay())
                    continue
                raise self._provider_error(
                    "OpenRouter request timed out", stage=LLMFailureStage.TIMEOUT,
                    task=task, profile=profile, http_status=504,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == 0:
                    logger.warning(
                        "openrouter_request_retry request_id=%s decision_run_id=%s task=%s configured_model=%s failure_stage=%s attempt=%s error=%s",
                        request_id, decision_run_id, task.value, profile.model, LLMFailureStage.NETWORK.value, attempt + 1, type(exc).__name__,
                    )
                    await asyncio.sleep(self._retry_delay())
                    continue
                raise self._provider_error(
                    "OpenRouter network connection error", stage=LLMFailureStage.NETWORK,
                    task=task, profile=profile,
                ) from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 0:
                    logger.warning(
                        "openrouter_request_retry request_id=%s decision_run_id=%s task=%s configured_model=%s failure_stage=%s status_code=%s attempt=%s",
                        request_id, decision_run_id, task.value, profile.model, LLMFailureStage.HTTP.value, response.status_code, attempt + 1,
                    )
                    await asyncio.sleep(self._retry_delay(response))
                    continue
            break

        assert response is not None
        duration_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            if request_context is not None:
                request_context["openrouter_raw_response"] = response.text
            try:
                error_data = response.json()
                error_metadata = self._metadata(error_data) if isinstance(error_data, dict) else {}
            except Exception:
                error_metadata = {}
            if response.status_code in (401, 403):
                message, status = "OpenRouter authentication failed", 401
            elif response.status_code == 402:
                message, status = "OpenRouter insufficient credits", 402
            elif response.status_code == 429:
                message, status = "OpenRouter rate limit exceeded", 429
            elif response.status_code >= 500:
                message, status = f"OpenRouter upstream server error ({response.status_code})", 502
            else:
                message, status = f"OpenRouter HTTP error ({response.status_code})", response.status_code
            response_text = response.text.lower()
            stage = (
                LLMFailureStage.TOKEN_LIMIT
                if response.status_code == 400 and any(token in response_text for token in ("max_tokens", "token limit", "context length", "token quota"))
                else LLMFailureStage.STRUCTURED_OUTPUT_FAILURE
                if response.status_code == 400 and profile.structured_output
                else LLMFailureStage.HTTP
            )
            logger.warning(
                "openrouter_request_failed request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s failure_stage=%s status_code=%s duration_ms=%s",
                request_id, decision_run_id, task.value, profile.model,
                error_metadata.get("resolved_model"), error_metadata.get("resolved_provider"), stage.value, response.status_code, duration_ms,
            )
            raise self._provider_error(message, stage=stage, task=task, profile=profile, http_status=status, metadata=error_metadata)

        try:
            data = response.json()
        except Exception as exc:
            if request_context is not None:
                request_context["openrouter_raw_response"] = response.text
            raise self._provider_error(
                "OpenRouter returned an invalid response envelope", stage=LLMFailureStage.JSON_PARSE,
                task=task, profile=profile,
            ) from exc
        if not isinstance(data, dict):
            raise self._provider_error(
                "OpenRouter returned an invalid response envelope", stage=LLMFailureStage.CONTENT_EXTRACTION,
                task=task, profile=profile,
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self._provider_error(
                "OpenRouter response contained no completion choices", stage=LLMFailureStage.CONTENT_EXTRACTION,
                task=task, profile=profile, metadata=self._metadata(data),
            )
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise self._provider_error(
                "OpenRouter response contained no assistant message", stage=LLMFailureStage.CONTENT_EXTRACTION,
                task=task, profile=profile, metadata=self._metadata(data, choice),
            )
        raw_text = message.get("content")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise self._provider_error(
                "OpenRouter response content was empty", stage=LLMFailureStage.EMPTY_RESPONSE,
                task=task, profile=profile, metadata=self._metadata(data, choice),
            )

        # Preserve exactly what the model emitted for the public raw_response
        # field. Parsing below is only the internal validation path.
        if request_context is not None:
            request_context["openrouter_raw_content"] = raw_text
        metadata = self._metadata(data, choice)
        if request_context is not None:
            request_context["openrouter_metadata"] = metadata
        if self._is_token_limit(metadata):
            raise self._provider_error(
                "OpenRouter completion reached its token limit", stage=LLMFailureStage.TOKEN_LIMIT,
                task=task, profile=profile, metadata=metadata,
            )
        try:
            content = raw_text.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content
                content = content.rsplit("```", 1)[0].strip()
            # Strict structured output is a contract, not a best-effort repair.
            # Invalid JSON must reach the deterministic fallback path.
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed JSON must be an object")
        except Exception as exc:
            logger.warning(
                "openrouter_request_failed request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s failure_stage=%s duration_ms=%s error=%s",
                request_id, decision_run_id, task.value, profile.model, metadata["resolved_model"], metadata["resolved_provider"],
                LLMFailureStage.JSON_PARSE.value, duration_ms, type(exc).__name__,
            )
            raise self._provider_error(
                "OpenRouter returned invalid JSON", stage=LLMFailureStage.JSON_PARSE,
                task=task, profile=profile, metadata=metadata,
            ) from exc

        logger.info(
            "openrouter_request_completed request_id=%s decision_run_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s routing_strategy=%s routing_attempt=%s status_code=%s duration_ms=%s finish_reason=%s native_finish_reason=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s reasoning_tokens=%s cost=%s",
            request_id, decision_run_id, task.value, profile.model, metadata["resolved_model"], metadata["resolved_provider"],
            metadata["routing_strategy"], metadata["routing_attempt"], response.status_code, duration_ms, metadata["finish_reason"], metadata["native_finish_reason"],
            metadata["prompt_tokens"], metadata["completion_tokens"], metadata["total_tokens"], metadata["reasoning_tokens"], metadata["cost"],
        )
        # This is an internal correlation side-channel only.  It lets a later
        # schema/grounding fallback log the provider selected for the successful
        # OpenRouter generation without adding transport metadata to the API
        # response or raw business payload.
        return parsed

    async def map_sheet(self, profile, canonical_schemas: dict, rule_suggestion: MappingSuggestion) -> MappingSuggestion:
        system = (
            "You map Excel sheet profiles to canonical schemas. Return exactly one JSON object, "
            "with sheet_type, confidence, column_mapping, warnings, errors. Map every source column "
            "to a valid schema field or null. Never add source columns. For inventory sheets, map a "
            "column that means goods-received/warehouse-receipt date (for example received date, receipt "
            "date, ngày nhập kho, ngày nhận hàng, or ngày về kho) to received_date. Do not map the "
            "inventory snapshot/count date to received_date; it belongs to snapshot_date. If no receipt-date "
            "column exists, leave received_date unmapped rather than inventing a value. For purchase_history "
            "sheets, map a goods-received/receipt date to received_date; map a purchase or invoice date to "
            "purchase_date."
        )
        user_payload = {
            "profile": profile.model_dump(mode="json"),
            "canonical_schemas": canonical_schemas,
            "rule_suggestion": rule_suggestion.model_dump(),
        }
        threshold = getattr(self.settings, "rule_confidence_threshold", 0.82)
        if not self.available:
            if rule_suggestion.confidence >= threshold or (rule_suggestion.sheet_type != "unknown" and rule_suggestion.column_mapping):
                fallback = rule_suggestion.model_copy(deep=True)
                fallback.source = "rule_fallback"
                fallback.requires_review = True
                return fallback
            raise LLMUnavailableError()

        started = time.monotonic()
        raw = None
        request_context: dict[str, Any] = {}
        try:
            raw = await self.generate_json(
                system,
                user_payload,
                task=LLMTask.EXCEL_MAPPING,
                request_context=request_context,
            )
            suggestion = self._validate_mapping_result(raw, profile)
            suggestion.raw_response = request_context.get("openrouter_raw_content", raw)
            return suggestion
        except Exception as exc:
            stage = self._failure_stage(exc)
            details = getattr(exc, "details", {})
            details = details if isinstance(details, dict) else {}
            metadata = request_context.get("openrouter_metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            logger.warning(
                "openrouter_mapping_failed request_id=%s task=%s configured_model=%s resolved_model=%s resolved_provider=%s failure_stage=%s duration_ms=%s error=%s fallback_to_rule=True",
                get_request_id(), LLMTask.EXCEL_MAPPING.value,
                self.task_profile(LLMTask.EXCEL_MAPPING).model,
                details.get("resolved_model") or metadata.get("resolved_model"),
                details.get("resolved_provider") or metadata.get("resolved_provider"),
                stage,
                int((time.monotonic() - started) * 1000),
                type(exc).__name__,
            )
            if rule_suggestion.confidence >= threshold or (rule_suggestion.sheet_type != "unknown" and rule_suggestion.column_mapping):
                fallback = rule_suggestion.model_copy(deep=True)
                fallback.source = "rule_fallback"
                fallback.requires_review = True
                fallback.warnings.append(f"LLM mapping failed ({stage}); rule suggestion retained")
                fallback.raw_response = request_context.get(
                    "openrouter_raw_content",
                    {"failure_stage": stage, "reason": type(exc).__name__},
                )
                return fallback
            raise LLMUnavailableError() from exc

    def _validate_mapping_result(self, raw: dict[str, Any], profile) -> MappingSuggestion:
        columns = set(profile.columns)
        mapping = raw.get("column_mapping", {})
        if not isinstance(mapping, dict) or set(mapping) != columns:
            raise self._provider_error(
                "LLM must map every and only source column", stage=LLMFailureStage.SCHEMA_VALIDATION,
                task=LLMTask.EXCEL_MAPPING, profile=self.task_profile(LLMTask.EXCEL_MAPPING),
            )
        raw["source"] = "llm"
        raw["requires_review"] = False
        try:
            suggestion = MappingSuggestion.model_validate(raw)
        except PydanticValidationError as exc:
            raise self._provider_error(
                "LLM mapping failed schema validation", stage=LLMFailureStage.SCHEMA_VALIDATION,
                task=LLMTask.EXCEL_MAPPING, profile=self.task_profile(LLMTask.EXCEL_MAPPING),
            ) from exc
        threshold = getattr(self.settings, "rule_confidence_threshold", 0.82)
        return finalize_mapping(profile, suggestion, threshold)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Compatibility for internal imports and downstream extensions using the old name.
OpenRouterQwenProvider = OpenRouterLLMGateway
