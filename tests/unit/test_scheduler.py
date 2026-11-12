"""Unit tests for dynamic ProactiveScanner anchor date and APScheduler LupScheduler."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.batch_ingestor import BatchIngestor, FinanceEventInput
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.jobs.proactive_scan import ProactiveScanner
from lup_ai.jobs.scheduler import LupScheduler
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_proactive_scanner_defaults_anchor_date_to_today(tmp_path: Path) -> None:
    today = date.today()
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(output_dir=data_dir, days=30, seed=7, end_date=today, sqlite_path=db_path)

    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    report = ProactiveScanner(store=store, repo=repo).run(lookback_days=30)

    assert report.scan_date == today.isoformat()


def test_lup_scheduler_registers_jobs_runs_cycle_and_lifespan(tmp_path: Path) -> None:
    today = date.today()
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(output_dir=data_dir, days=45, seed=9, end_date=today, sqlite_path=db_path)

    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        lup_scheduler_enabled=True,
        lup_proactive_scan_interval_minutes=15,
        lup_batch_flush_interval_minutes=5,
        lup_proactive_scan_lookback_days=30,
    )
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    ingestor = BatchIngestor(settings=settings, store=store, local_csv_repo=repo)
    ingestor.capture_finance(
        FinanceEventInput(
            tx_timestamp=f"{today.isoformat()} 11:00:00",
            amount=18000.0,
            category_raw="SCHEDULER_TEST",
            direction="expense",
            is_discretionary=True,
        )
    )

    scheduler = LupScheduler(settings=settings, store=store, repo=repo, ingestor=ingestor)
    scan_res, flush_res = scheduler.run_cycle_once()
    assert scan_res is not None
    assert scan_res.scan_date == today.isoformat()
    assert flush_res is not None
    assert flush_res.flushed_count == 1
    assert store.list_pending_ingest_events() == []

    assert scheduler.start() is True
    try:
        st = scheduler.status()
        assert st.running is True
        assert {job.id for job in st.jobs} == {"proactive_scan_job", "batch_flush_job"}
        assert st.last_scan_at is not None
        assert st.last_flush_at is not None
    finally:
        scheduler.shutdown(wait=False)
    assert scheduler.is_running is False

    app = create_app(
        settings=settings,
        store=store,
        repo=repo,
        enable_scheduler=True,
    )
    with TestClient(app) as client:
        resp = client.get("/v1/jobs/scheduler")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is True
        assert len(body["jobs"]) == 2
    assert app.state.scheduler.is_running is False

