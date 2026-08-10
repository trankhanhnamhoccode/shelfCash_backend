from functools import lru_cache
from pathlib import Path

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
    llm_provider: str = "disabled"
    qwen_model_id: str = "Qwen/Qwen3-4B"
    qwen_load_in_4bit: bool = True
    qwen_max_new_tokens: int = 900
    qwen_timeout_seconds: int = 180
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
    decision_engine_mode: str = "legacy"
    decision_scenario_count: int = 100
    decision_random_seed: int = 42
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
