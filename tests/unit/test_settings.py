"""Unit tests for typed runtime configuration."""

import pytest
from pydantic import ValidationError

from lup_ai.config.settings import Settings


def test_default_settings_enforce_masked_cloud_and_bogota_tz() -> None:
    settings = Settings(_env_file=None)
    assert settings.lup_privacy_mode == "masked_cloud"
    assert settings.lup_timezone == "America/Bogota"
    assert settings.snowflake_role_agent == "LUP_AGENT_RO"
    assert settings.snowflake_schema_semantic == "SEMANTIC"


def test_unsupported_privacy_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, lup_privacy_mode="strict_local")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, lup_privacy_mode="full_hybrid")  # type: ignore[arg-type]
