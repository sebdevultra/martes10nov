"""Unit tests for Snowflake credential validation and bootstrap script."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lup_ai.config.settings import Settings
from lup_ai.data.snowflake_client import (
    SnowflakeConfigurationError,
    build_snowflake_connect_kwargs,
    has_valid_snowflake_credentials,
)
from lup_ai.seeds.bootstrap_snowflake import bootstrap_snowflake


def test_placeholder_snowflake_credentials_are_rejected() -> None:
    placeholder_cfg = Settings(
        _env_file=None,
        snowflake_account="your_account_locator",
        snowflake_user="your_snowflake_user",
        snowflake_password="your_snowflake_password",
    )
    assert has_valid_snowflake_credentials(placeholder_cfg) is False

    real_cfg = Settings(
        _env_file=None,
        snowflake_account="xy12345.us-east-1",
        snowflake_user="lup_admin",
        snowflake_password="secret_value",
    )
    assert has_valid_snowflake_credentials(real_cfg) is True


class _FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed
        self._last_count = 180

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, sql: str) -> None:
        self._executed.append(sql)
        if "VW_GOAL_PROGRESS" in sql:
            self._last_count = 4
        elif "VW_CROSS_DOMAIN_DAILY" in sql:
            self._last_count = 180

    def fetchone(self) -> tuple[int]:
        return (self._last_count,)


class _FakeSnowflakeConn:
    def __init__(self) -> None:
        self.strings: list[str] = []
        self.statements: list[str] = []
        self.closed = False

    def execute_string(self, sql_text: str, remove_comments: bool = False) -> list[Any]:
        self.strings.append(sql_text)
        return []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.statements)

    def close(self) -> None:
        self.closed = True


def test_bootstrap_snowflake_runs_ddls_uploads_and_verifies_counts(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "local.db"
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        snowflake_account="xy12345.us-east-1",
        snowflake_user="lup_admin",
        snowflake_password="secret_value",
    )
    fake_conn = _FakeSnowflakeConn()

    report = bootstrap_snowflake(
        settings=settings,
        project_root=Path("."),
        data_dir=data_dir,
        dry_run=False,
        connector_factory=lambda _cfg: fake_conn,
    )

    assert report.ddl_scripts_executed == 5
    assert len(report.files_uploaded) == 3
    assert report.cross_domain_rows == 180
    assert report.goals_rows == 4
    assert len(fake_conn.strings) == 6
    assert any("PUT 'file://" in stmt for stmt in fake_conn.statements)
    assert any("ALTER DYNAMIC TABLE" in stmt for stmt in fake_conn.statements)
    assert fake_conn.closed is True


def test_build_snowflake_connect_kwargs_uses_jwt_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / "test_key.p8"
    key_file.write_text("dummy-pem", encoding="utf-8")

    cfg = Settings(
        _env_file=None,
        snowflake_account="myorg-myacct",
        snowflake_user="sebdevultra",
        snowflake_private_key_path=str(key_file),
        snowflake_private_key_passphrase="secret-passphrase",
    )
    kwargs = build_snowflake_connect_kwargs(cfg, role="LUP_AGENT_RO")

    assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert kwargs["private_key_file"] == str(key_file)
    assert kwargs["private_key_password"] == "secret-passphrase"
    assert "password" not in kwargs
    assert kwargs["role"] == "LUP_AGENT_RO"


def test_build_snowflake_connect_kwargs_missing_key_file_raises(tmp_path: Path) -> None:
    cfg = Settings(
        _env_file=None,
        snowflake_account="myorg-myacct",
        snowflake_user="sebdevultra",
        snowflake_private_key_path=str(tmp_path / "missing.p8"),
    )
    with pytest.raises(SnowflakeConfigurationError, match="not found"):
        build_snowflake_connect_kwargs(cfg)
