from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "shelfcash-backend"
    app_version: str = "1.0.0"
    environment: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Retained for legacy configuration and health output. Runtime task routing
    # is controlled only by the two task-specific model settings below.
    openrouter_model: str = "qwen/qwen3.5-9b"
    openrouter_timeout_seconds: int = 60
    openrouter_max_new_tokens: int = 1800
    openrouter_mapping_model: str = "qwen/qwen3.5-9b"
    openrouter_mapping_timeout_seconds: float = 60
    openrouter_mapping_max_tokens: int = 1800
    openrouter_mapping_temperature: float = 0.0
    openrouter_mapping_reasoning_enabled: bool = False
    openrouter_mapping_structured_output: bool = True
    openrouter_mapping_strict_schema: bool = True
    openrouter_mapping_require_parameters: bool = True
    openrouter_narrative_model: str = "qwen/qwen3.5-9b"
    openrouter_narrative_timeout_seconds: float = 60
    openrouter_narrative_max_tokens: int = 1200
    openrouter_narrative_temperature: float = 0.0
    openrouter_narrative_reasoning_enabled: bool = False
    openrouter_narrative_structured_output: bool = True
    openrouter_narrative_strict_schema: bool = True
    openrouter_narrative_require_parameters: bool = True
    openrouter_summary_model: str = "qwen/qwen3.5-9b"
    openrouter_summary_timeout_seconds: float = 60
    openrouter_summary_max_tokens: int = 900
    openrouter_summary_temperature: float = 0.0
    openrouter_summary_reasoning_enabled: bool = False
    openrouter_summary_structured_output: bool = True
    openrouter_summary_strict_schema: bool = True
    openrouter_summary_require_parameters: bool = True
    # Manager-facing strategy wording is deterministic unless an explicit,
    # validated opt-in enables the existing guarded Qwen polish path.
    strategy_expression_mode: Literal["deterministic", "llm_polish"] = "deterministic"
    # Retained for callers/configuration that used the old setting. Narrative
    # requests now use OPENROUTER_NARRATIVE_MAX_TOKENS.
    decision_narrative_max_new_tokens: int = 1200
    rule_confidence_threshold: float = 0.82
    max_files_per_request: int = 10
    max_file_size_mb: int = 12
    max_total_upload_size_mb: int = 50
    max_sheets_per_file: int = 30
    max_rows_per_sheet: int = 100_000
    sample_rows_per_sheet: int = 8
    upload_dir: Path = Path("runtime/uploads")
    result_dir: Path = Path("runtime/results")
    forecast_artifact_root: Path = Path("runtime/forecast_artifacts")
    forecast_default_model_version: str = "forecast-core-v0.1.0"
    forecast_history_days: int = 365
    forecast_max_horizon: int = 7
    forecast_debug_export: bool = False
    forecast_export_dir: Path = Path("forecast_debug")
    # Phase-1 provider selection is deliberately production-locked.  The new
    # core may only run alongside it until an explicit later phase changes this.
    forecast_core_provider: str = "existing"
    forecast_shadow_provider: str = "disabled"
    forecast_shadow_enabled: bool = False
    forecast_shadow_artifact_root: Path = Path("runtime/forecast_shadow_artifacts")
    decision_engine_mode: str = "legacy"
    decision_scenario_count: int = 100
    decision_random_seed: int = 42
    decision_scenario_method: str = "residual_bootstrap"
    database_url: str = "sqlite:///runtime/shelfcash.db"
    shelfcash_api_key: str = ""
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
