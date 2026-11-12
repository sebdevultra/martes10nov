"""Typed runtime settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for LUP AI runtime, Snowflake, local SQLite, and Gemini 3.8."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    lup_env: Literal["development", "staging", "production"] = "development"
    lup_log_level: str = "INFO"
    lup_timezone: str = "America/Bogota"

    # Single enforced privacy policy; raw PII must be masked in Python before Gemini calls.
    lup_privacy_mode: Literal["masked_cloud"] = "masked_cloud"

    lup_sqlite_path: Path = Path("./data/lup_ai_local.db")
    lup_cache_ttl_seconds: int = Field(default=3600, ge=60)

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_fallback_models: str = "gemini-3.5-flash,gemini-3.6-flash,gemini-3.7-flash"
    gemini_timeout_seconds: int = Field(default=45, ge=5, le=180)

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_private_key_path: str = ""
    snowflake_private_key_passphrase: str = ""
    snowflake_role_agent: str = "LUP_AGENT_RO"
    snowflake_role_ingest: str = "LUP_INGEST_RW"
    snowflake_warehouse: str = "LUP_WH_XS"
    snowflake_database: str = "LUP_AI_DB"
    snowflake_schema_semantic: str = "SEMANTIC"
    snowflake_statement_timeout_seconds: int = Field(default=15, ge=5, le=60)

    lup_api_host: str = "127.0.0.1"
    lup_api_port: int = Field(default=8000, ge=1, le=65535)

    lup_scheduler_enabled: bool = True
    lup_proactive_scan_interval_minutes: int = Field(default=720, ge=1, le=10080)
    lup_batch_flush_interval_minutes: int = Field(default=60, ge=1, le=1440)
    lup_proactive_scan_lookback_days: int = Field(default=90, ge=14, le=365)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
