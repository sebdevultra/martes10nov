"""Typer + Rich CLI entry points for LUP AI Phase 1."""

from __future__ import annotations

from typing import Literal

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lup_ai import __version__
from lup_ai.agent.orchestrator import LupOrchestrator, build_default_repository
from lup_ai.config.settings import get_settings
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import QueryCatalog
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.llm.gemini_client import GeminiClient

app = typer.Typer(help="LUP AI Personal Decision Intelligence CLI.")
console = Console()


@app.command()
def status() -> None:
    """Display active configuration, privacy mode, and data/LLM connectivity status."""
    settings = get_settings()
    repo = build_default_repository(settings)
    sf_ok = SnowflakeAnalyticsRepository(settings).is_configured()
    gemini_ok = GeminiClient(settings).is_configured()

    table = Table(title=f"LUP AI v{__version__} Status")
    table.add_column("Component", style="bold")
    table.add_column("Value")
    table.add_row("Privacy Mode", settings.lup_privacy_mode)
    table.add_row("Data Backend", repo.backend_name)
    table.add_row("Snowflake Configured", str(sf_ok))
    table.add_row("Gemini 3.8 Configured", str(gemini_ok))
    table.add_row("Gemini Model", settings.gemini_model)
    table.add_row("SQLite Path", str(settings.lup_sqlite_path))
    console.print(table)


@app.command()
def goals() -> None:
    """List active goals and hard constraints."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    repo = build_default_repository(settings)
    catalog = QueryCatalog(repo=repo, store=store, cache_ttl_seconds=settings.lup_cache_ttl_seconds)

    try:
        rows = catalog.query_goals().rows
    except FileNotFoundError:
        rows = store.list_active_goals()

    table = Table(title="Objetivos Activos")
    table.add_column("Dominio")
    table.add_column("Titulo")
    table.add_column("Meta")
    table.add_column("Observado")
    for row in rows:
        target = f"{row['comparison_operator']} {row['target_value']} {row['unit']}"
        observed = str(row.get("latest_observed_value", "-"))
        table.add_row(str(row["domain"]), str(row["title"]), target, observed)
    console.print(table)


@app.command()
def query(
    domain: Literal[
        "finance", "habits_sleep", "time_calendar", "cross_domain", "goals"
    ] = typer.Argument(..., help="Domain view to query."),
    days: int = typer.Option(30, min=1, max=365, help="Lookback window in days."),
) -> None:
    """Query Level-1 semantic aggregates for a domain."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    repo = build_default_repository(settings)
    catalog = QueryCatalog(repo=repo, store=store, cache_ttl_seconds=settings.lup_cache_ttl_seconds)
    start_date, end_date = catalog.default_range(days=days)

    if domain == "finance":
        res = catalog.query_finance(start_date, end_date)
    elif domain == "habits_sleep":
        res = catalog.query_habits_sleep(start_date, end_date)
    elif domain == "time_calendar":
        res = catalog.query_time_calendar(start_date, end_date)
    elif domain == "goals":
        res = catalog.query_goals()
    else:
        res = catalog.query_cross_domain(start_date, end_date)

    table = Table(title=f"{res.source_view} ({res.backend}, cache_hit={res.cache_hit})")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    for key, val in res.aggregates.items():
        table.add_row(str(key), str(val))
    console.print(table)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural-language question for LUP AI."),
    session_id: str | None = typer.Option(None, help="Optional session identifier."),
) -> None:
    """Ask LUP AI a question through the masked_cloud pipeline."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    orchestrator = LupOrchestrator(settings=settings, store=store)
    result = orchestrator.ask(question=question, session_id=session_id)

    console.print(Panel(result.answer, title=f"LUP AI ({result.domain} | {result.model_used})"))
    if result.key_observations:
        for obs in result.key_observations:
            console.print(f"- {obs}")
    console.print(
        f"Evidencia: {result.evidence.source_view} | backend={result.evidence.backend} "
        f"| rows={result.evidence.row_count} | privacy={result.privacy_mode} "
        f"| anonymizer={result.anonymizer_engine} | session={result.session_id}"
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="HTTP port."),
) -> None:
    """Start the FastAPI REST server."""
    uvicorn.run("lup_ai.api.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
