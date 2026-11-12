"""Smoke tests for Typer CLI commands."""

from typer.testing import CliRunner

from lup_ai.cli.app import app


def test_cli_help_and_commands_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "status",
        "goals",
        "query",
        "trends",
        "anomalies",
        "simulate",
        "ask",
        "decisions",
        "feedback",
        "scan",
        "capture-finance",
        "capture-sleep",
        "capture-time",
        "flush",
        "sync-simulated",
        "bootstrap-snowflake",
        "ui",
        "serve",
    ):
        assert cmd in result.stdout
