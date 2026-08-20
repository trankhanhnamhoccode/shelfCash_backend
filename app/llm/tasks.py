from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LLMTask(str, Enum):
    """Known LLM tasks. Business callers select a task, never a model."""

    EXCEL_MAPPING = "excel_mapping"
    DECISION_NARRATIVE = "decision_narrative"
    PLAN_SUMMARY = "plan_summary"


class LLMFailureStage(str, Enum):
    HTTP = "HTTP"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CONTENT_EXTRACTION = "CONTENT_EXTRACTION"
    JSON_PARSE = "JSON_PARSE"
    STRUCTURED_OUTPUT_FAILURE = "STRUCTURED_OUTPUT_FAILURE"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    BUSINESS_VALIDATION = "BUSINESS_VALIDATION"
    GROUNDING = "GROUNDING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OpenRouterTaskProfile:
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    reasoning_enabled: bool
    structured_output: bool
    strict_schema: bool
    require_parameters: bool
