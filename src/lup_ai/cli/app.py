"""Typer + Rich CLI entry points for LUP AI."""

from __future__ import annotations

import subprocess
import sys
from typing import Literal

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lup_ai import __version__
from lup_ai.agent.orchestrator import LupOrchestrator, build_default_repository
from lup_ai.config.settings import get_settings
from lup_ai.connectors.simulated_sources import SimulatedConnectorHub
from lup_ai.data.batch_ingestor import (
    BatchIngestor,
    FinanceEventInput,
    HabitsSleepEventInput,
    TimeBlockEventInput,
)
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import QueryCatalog
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.engines.simulation_engine import SimulationScenario
from lup_ai.jobs.proactive_scan import ProactiveScanner
from lup_ai.llm.gemini_client import GeminiClient
from lup_ai.memory.decision_tracker import DecisionTracker
from lup_ai.seeds.bootstrap_snowflake import bootstrap_snowflake

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
def trends(
    days: int = typer.Option(90, min=14, max=365, help="Lookback window in days."),
) -> None:
    """Analyze deterministic rolling trends and cross-domain correlations."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    orchestrator = LupOrchestrator(settings=settings, store=store)
    analysis, evidence = orchestrator.analyze_trends(days=days)

    table = Table(title=f"Tendencias ({analysis.start_date} a {analysis.end_date})")
    table.add_column("Dominio")
    table.add_column("Metrica")
    table.add_column("7d")
    table.add_column("30d")
    table.add_column("WoW %")
    table.add_column("Direccion")
    for item in analysis.trends:
        table.add_row(
            item.domain,
            item.metric_name,
            str(item.mean_7d),
            str(item.mean_30d),
            str(item.wow_delta_pct),
            item.direction,
        )
    console.print(table)

    if analysis.correlations:
        corr_table = Table(title="Correlaciones Cruzadas (No Causales)")
        corr_table.add_column("Origen")
        corr_table.add_column("Destino")
        corr_table.add_column("Lag")
        corr_table.add_column("Spearman rho")
        corr_table.add_column("Fuerza")
        for corr in analysis.correlations:
            corr_table.add_row(
                corr.source_metric,
                corr.target_metric,
                f"{corr.lag_days}d",
                str(corr.spearman_rho),
                corr.strength,
            )
        console.print(corr_table)
    console.print(f"Evidencia: {evidence.source_view} | rows={evidence.row_count}")


@app.command()
def anomalies(
    days: int = typer.Option(90, min=14, max=365, help="Lookback window in days."),
) -> None:
    """Scan for statistical anomalies, constraint streaks, and opportunities."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    orchestrator = LupOrchestrator(settings=settings, store=store)
    scan, recommendations, evidence = orchestrator.scan_anomalies(days=days)

    table = Table(title=f"Anomalias ({scan.start_date} a {scan.end_date})")
    table.add_column("Fecha")
    table.add_column("Severidad")
    table.add_column("Metrica")
    table.add_column("Esperado")
    table.add_column("Observado")
    table.add_column("Z-mod")
    for item in scan.anomalies:
        table.add_row(
            item.detected_date,
            item.severity,
            item.metric_name,
            str(item.expected_value),
            str(item.actual_value),
            str(item.modified_z_score),
        )
    console.print(table)

    if scan.opportunities:
        opp_table = Table(title="Oportunidades Detectadas")
        opp_table.add_column("Dominio")
        opp_table.add_column("Titulo")
        opp_table.add_column("Ganancia Estimada")
        for opp in scan.opportunities:
            opp_table.add_row(opp.domain, opp.title, f"+{opp.estimated_gain} {opp.unit}")
        console.print(opp_table)

    for rec in recommendations:
        body = f"Accion: {rec.action}\nImpacto: {rec.expected_impact}\nConfianza: {rec.confidence}"
        console.print(Panel(body, title=f"Recomendacion ({rec.domain})"))
    console.print(f"Evidencia: {evidence.source_view} | rows={evidence.row_count}")


@app.command()
def simulate(
    horizon_days: int = typer.Option(90, min=14, max=365, help="Projection horizon in days."),
    discretionary_pct: float = typer.Option(0.0, min=-0.95, max=2.0, help="e.g. -0.25 for -25%."),
    extra_weekly_savings: float = typer.Option(0.0, help="Extra savings per week in COP."),
    sleep_target: float | None = typer.Option(None, min=3.0, max=12.0, help="Target sleep hours."),
    deep_work_delta: float = typer.Option(0.0, min=-30.0, max=40.0, help="Weekly deep work delta."),
    baseline_days: int = typer.Option(180, min=14, max=365, help="Historical baseline window."),
) -> None:
    """Run deterministic What-If simulation across Finance, Habits/Sleep, and Time."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    orchestrator = LupOrchestrator(settings=settings, store=store)
    scenario = SimulationScenario(
        horizon_days=horizon_days,
        discretionary_spend_delta_pct=discretionary_pct,
        extra_weekly_savings=extra_weekly_savings,
        sleep_hours_target=sleep_target,
        deep_work_delta_hours_per_week=deep_work_delta,
    )
    sim, recommendations, evidence = orchestrator.simulate(
        scenario=scenario, baseline_days=baseline_days
    )

    traj_table = Table(
        title=f"Simulacion What-If ({sim.horizon_days}d, base {sim.baseline_days_used}d)"
    )
    traj_table.add_column("Metrica")
    traj_table.add_column("Base Line")
    traj_table.add_column("Conservador p25")
    traj_table.add_column("Base p50")
    traj_table.add_column("Optimista p75")
    traj_table.add_column("Delta p50")
    for name, traj in sim.trajectories.items():
        traj_table.add_row(
            f"{name} ({traj.unit})",
            str(traj.baseline_projected),
            str(traj.conservative_p25),
            str(traj.base_p50),
            str(traj.optimistic_p75),
            str(traj.delta_vs_baseline_p50),
        )
    console.print(traj_table)

    if sim.goal_projections:
        goal_table = Table(title="Proyeccion de Metas")
        goal_table.add_column("Meta")
        goal_table.add_column("Objetivo")
        goal_table.add_column("p50")
        goal_table.add_column("Prob Exito")
        goal_table.add_column("Estado")
        for g in sim.goal_projections:
            goal_table.add_row(
                g.title,
                f"{g.target_value} {g.unit}",
                str(g.projected_p50),
                f"{g.probability_of_success_pct}%",
                g.status_label,
            )
        console.print(goal_table)

    for col in sim.constraint_collisions:
        console.print(f"[{col.severity.upper()}] {col.description}")
    for rec in recommendations:
        console.print(Panel(f"{rec.action}\n{rec.expected_impact}", title="Recomendacion Simulada"))
    console.print(f"Evidencia: {evidence.source_view} | rows={evidence.row_count}")


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
def decisions(
    status: Literal["pending", "accepted", "dismissed", "completed"] | None = typer.Option(
        None, help="Filter by decision status."
    ),
) -> None:
    """List decisions inbox and historical feedback state."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    tracker = DecisionTracker(store)
    rows = tracker.list_decisions(user_action=status)
    summary = tracker.adherence_summary()

    table = Table(
        title=(
            f"Decisiones (total={summary.total_decisions}, "
            f"aceptacion={summary.acceptance_rate_pct}%, mejora={summary.improvement_rate_pct}%)"
        )
    )
    table.add_column("ID")
    table.add_column("Dominio")
    table.add_column("Titulo")
    table.add_column("Estado")
    table.add_column("Origen")
    for row in rows:
        table.add_row(row.decision_id, row.domain, row.title, row.user_action, row.source_type)
    console.print(table)


@app.command()
def feedback(
    decision_id: str = typer.Argument(..., help="Decision ID to update."),
    action: Literal["accepted", "dismissed", "completed"] = typer.Argument(
        ..., help="Decision action."
    ),
    note: str | None = typer.Option(None, help="Optional feedback note."),
) -> None:
    """Record user acceptance or dismissal for a decision."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    tracker = DecisionTracker(store)
    try:
        updated = tracker.record_feedback(
            decision_id=decision_id,
            user_action=action,
            user_feedback=note,
        )
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Decision {updated.decision_id} actualizada a '{updated.user_action}'.")


@app.command()
def scan(
    days: int = typer.Option(90, min=14, max=365, help="Lookback window in days."),
    force_outcomes: bool = typer.Option(
        False, help="Force evaluation of accepted decisions immediately."
    ),
) -> None:
    """Run proactive anomaly/opportunity scan and Decision Outcome Loop evaluation."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    repo = build_default_repository(settings)
    report = ProactiveScanner(store=store, repo=repo).run(
        lookback_days=days,
        evaluate_outcomes_force=force_outcomes,
    )
    console.print(
        f"Scan {report.scan_date} ({report.data_backend}): "
        f"anomalias={report.anomalies_found}, oportunidades={report.opportunities_found}, "
        f"decisiones={len(report.persisted_decision_ids)}, "
        f"outcomes_evaluados={len(report.evaluated_outcomes)}"
    )


@app.command("capture-finance")
def capture_finance(
    amount: float = typer.Option(..., min=0.01, help="Transaction amount."),
    category: str = typer.Option(..., help="Raw category label."),
    direction: Literal["income", "expense", "saving"] = typer.Option("expense"),
    discretionary: bool = typer.Option(True),
) -> None:
    """Buffer a finance event in local SQLite queue."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    res = BatchIngestor(settings=settings, store=store).capture_finance(
        FinanceEventInput(
            amount=amount,
            direction=direction,
            category_raw=category,
            is_discretionary=discretionary,
        )
    )
    console.print(f"Buffered finance event {res.entry_id} (status={res.sync_status}).")


@app.command("capture-sleep")
def capture_sleep(
    sleep_hours: float = typer.Option(..., min=0.0, max=24.0),
    exercise_minutes: int = typer.Option(0, min=0),
    energy_score: float = typer.Option(7.0, min=1.0, max=10.0),
    log_date: str | None = typer.Option(None, help="YYYY-MM-DD"),
) -> None:
    """Buffer a daily habits/sleep record in local SQLite queue."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    res = BatchIngestor(settings=settings, store=store).capture_habits_sleep(
        HabitsSleepEventInput(
            log_date=log_date,
            sleep_hours=sleep_hours,
            exercise_minutes=exercise_minutes,
            energy_score=energy_score,
        )
    )
    console.print(f"Buffered habits_sleep event {res.entry_id} (status={res.sync_status}).")


@app.command("capture-time")
def capture_time(
    duration_minutes: int = typer.Option(..., min=1, max=1440),
    block_type: Literal["DEEP_WORK", "MEETING", "LEISURE", "ADMIN", "REST"] = typer.Option(
        "DEEP_WORK"
    ),
    domain_tag: str = typer.Option("work"),
) -> None:
    """Buffer a time/calendar block in local SQLite queue."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    res = BatchIngestor(settings=settings, store=store).capture_time_block(
        TimeBlockEventInput(
            duration_minutes=duration_minutes,
            block_type=block_type,
            domain_tag=domain_tag,
        )
    )
    console.print(f"Buffered time_calendar event {res.entry_id} (status={res.sync_status}).")


@app.command()
def flush() -> None:
    """Flush pending buffered events in batch to Snowflake or local CSV mirror."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    repo = build_default_repository(settings)
    ingestor = BatchIngestor(
        settings=settings,
        store=store,
        local_csv_repo=repo if isinstance(repo, LocalCsvAnalyticsRepository) else None,
    )
    res = ingestor.flush_batch()
    console.print(f"Flushed {res.flushed_count} events to {res.target_backend}: {res.by_domain}")


@app.command("sync-simulated")
def sync_simulated(
    days: int = typer.Option(3, min=1, max=30, help="Days of simulated external feeds."),
    seed: int = typer.Option(101, help="Deterministic simulation seed."),
    no_flush: bool = typer.Option(False, help="Keep events buffered in SQLite without flushing."),
) -> None:
    """Pull simulated Bank, Wearable, and Calendar feeds into BatchIngestor."""
    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    repo = build_default_repository(settings)
    ingestor = BatchIngestor(
        settings=settings,
        store=store,
        local_csv_repo=repo if isinstance(repo, LocalCsvAnalyticsRepository) else None,
    )
    report = SimulatedConnectorHub(ingestor).sync_all(
        days=days,
        seed=seed,
        auto_flush=not no_flush,
    )
    console.print(
        f"Simulated sync ({report.source_mode}): captured={report.events_captured}, "
        f"domains={report.by_domain}, flushed={report.flushed}"
    )


@app.command()
def ui(
    port: int = typer.Option(8501, help="Streamlit HTTP port."),
) -> None:
    """Launch the Streamlit dashboard connected to LUP AI FastAPI."""
    from lup_ai.ui.streamlit_app import get_app_path

    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(get_app_path()),
            "--server.port",
            str(port),
        ],
        check=True,
    )


@app.command("bootstrap-snowflake")
def bootstrap_sf(
    dry_run: bool = typer.Option(False, help="Validate DDL/seed files without connecting."),
) -> None:
    """Provision Snowflake LUP_AI_DB DDLs (01..05), upload synthetic CSVs, and verify views."""
    settings = get_settings()
    try:
        report = bootstrap_snowflake(settings=settings, dry_run=dry_run)
    except Exception as exc:
        console.print(f"Error en bootstrap Snowflake: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Snowflake bootstrap (dry_run={report.dry_run}): "
        f"ddl_scripts={report.ddl_scripts_executed}, "
        f"uploaded={len(report.files_uploaded)}, "
        f"cross_domain_rows={report.cross_domain_rows}, "
        f"goals_rows={report.goals_rows}"
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
