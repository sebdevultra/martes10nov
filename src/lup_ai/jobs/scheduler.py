"""APScheduler background runner for periodic proactive scans and batch Snowflake flushes."""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import BaseModel, Field

from lup_ai.config.settings import Settings, get_settings
from lup_ai.data.batch_ingestor import BatchIngestor, FlushResult
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.jobs.proactive_scan import ProactiveScanner, ProactiveScanReport

_LOGGER = logging.getLogger(__name__)


class ScheduledJobInfo(BaseModel):
    """Metadata describing a registered APScheduler interval job."""

    id: str
    name: str
    next_run_time: str | None = None


class SchedulerStatus(BaseModel):
    """Current runtime state of LupScheduler."""

    enabled: bool
    running: bool
    timezone: str
    proactive_scan_interval_minutes: int
    batch_flush_interval_minutes: int
    jobs: list[ScheduledJobInfo] = Field(default_factory=list)
    last_scan_at: str | None = None
    last_flush_at: str | None = None
    last_error: str | None = None


class LupScheduler:
    """Coordinate periodic ProactiveScanner and BatchIngestor.flush_batch execution."""

    def __init__(
        self,
        settings: Settings,
        store: LocalStore,
        repo: AnalyticsRepository,
        ingestor: BatchIngestor | None = None,
        scanner: ProactiveScanner | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._repo = repo
        self._ingestor = ingestor or BatchIngestor(
            settings=settings,
            store=store,
            local_csv_repo=(repo if isinstance(repo, LocalCsvAnalyticsRepository) else None),
        )
        self._scanner = scanner or ProactiveScanner(store=store, repo=repo)
        self._scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.lup_timezone))
        self._last_scan_at: str | None = None
        self._last_flush_at: str | None = None
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        """Return True while the underlying BackgroundScheduler thread is active."""
        return bool(self._scheduler.running)

    def run_proactive_scan_job(self) -> ProactiveScanReport | None:
        """Run a scheduled proactive scan without crashing the scheduler loop."""
        try:
            report = self._scanner.run(
                lookback_days=self._settings.lup_proactive_scan_lookback_days
            )
            self._last_scan_at = datetime.now(UTC).isoformat()
            _LOGGER.info(
                "Scheduled proactive scan completed: anomalies=%d decisions=%d outcomes=%d",
                report.anomalies_found,
                len(report.persisted_decision_ids),
                len(report.evaluated_outcomes),
            )
            return report
        except Exception as exc:
            self._last_error = f"proactive_scan: {type(exc).__name__}: {exc}"
            _LOGGER.warning("Scheduled proactive scan failed: %s", self._last_error)
            return None

    def run_batch_flush_job(self) -> FlushResult | None:
        """Run a scheduled batch flush (events, decisions, outcomes) without crashing."""
        try:
            res = self._ingestor.flush_batch()
            self._last_flush_at = datetime.now(UTC).isoformat()
            _LOGGER.info(
                "Scheduled batch flush completed (%s): events=%d decisions=%d outcomes=%d",
                res.target_backend,
                res.flushed_count,
                res.decisions_synced,
                res.outcomes_synced,
            )
            return res
        except Exception as exc:
            self._last_error = f"batch_flush: {type(exc).__name__}: {exc}"
            _LOGGER.warning("Scheduled batch flush failed: %s", self._last_error)
            return None

    def run_cycle_once(self) -> tuple[ProactiveScanReport | None, FlushResult | None]:
        """Execute one immediate scan + flush cycle synchronously."""
        scan_report = self.run_proactive_scan_job()
        flush_result = self.run_batch_flush_job()
        return scan_report, flush_result

    def start(self) -> bool:
        """Register interval jobs and start BackgroundScheduler when enabled."""
        if not self._settings.lup_scheduler_enabled or self._scheduler.running:
            return False

        self._scheduler.add_job(
            self.run_proactive_scan_job,
            trigger="interval",
            minutes=self._settings.lup_proactive_scan_interval_minutes,
            id="proactive_scan_job",
            name="Proactive anomaly & outcome scan",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.run_batch_flush_job,
            trigger="interval",
            minutes=self._settings.lup_batch_flush_interval_minutes,
            id="batch_flush_job",
            name="Batch ingest & decision/outcome flush",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        self._scheduler.start()
        return True

    def shutdown(self, wait: bool = False) -> None:
        """Stop BackgroundScheduler cleanly if running."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def status(self) -> SchedulerStatus:
        """Return registered job metadata and recent execution timestamps."""
        jobs = [
            ScheduledJobInfo(
                id=str(job.id),
                name=str(job.name),
                next_run_time=job.next_run_time.isoformat() if job.next_run_time else None,
            )
            for job in self._scheduler.get_jobs()
        ]
        return SchedulerStatus(
            enabled=self._settings.lup_scheduler_enabled,
            running=bool(self._scheduler.running),
            timezone=self._settings.lup_timezone,
            proactive_scan_interval_minutes=self._settings.lup_proactive_scan_interval_minutes,
            batch_flush_interval_minutes=self._settings.lup_batch_flush_interval_minutes,
            jobs=jobs,
            last_scan_at=self._last_scan_at,
            last_flush_at=self._last_flush_at,
            last_error=self._last_error,
        )


def _build_default_scheduler(settings: Settings, data_dir: Path | None = None) -> LupScheduler:
    store = LocalStore(settings.lup_sqlite_path)
    sf_repo = SnowflakeAnalyticsRepository(settings)
    repo: AnalyticsRepository = (
        sf_repo
        if sf_repo.is_configured()
        else LocalCsvAnalyticsRepository(data_dir or Path("data/generated"))
    )
    return LupScheduler(settings=settings, store=store, repo=repo)


def main() -> None:
    """CLI entry point to run APScheduler daemon or a single scan+flush cycle."""
    parser = argparse.ArgumentParser(description="Run LUP AI APScheduler background worker.")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Execute one proactive scan + batch flush cycle immediately and exit.",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    settings = get_settings()
    scheduler = _build_default_scheduler(settings, data_dir=args.data_dir)

    if args.run_once:
        scan_report, flush_res = scheduler.run_cycle_once()
        anomalies = scan_report.anomalies_found if scan_report else "err"
        print(
            f"Cycle complete: scan_anomalies={anomalies}, "
            f"flush_events={flush_res.flushed_count if flush_res else 'err'}, "
            f"decisions_synced={flush_res.decisions_synced if flush_res else 'err'}, "
            f"outcomes_synced={flush_res.outcomes_synced if flush_res else 'err'}"
        )
        return

    started = scheduler.start()
    if not started:
        print("Scheduler is disabled (LUP_SCHEDULER_ENABLED=false).")
        return

    print(
        "LUP Scheduler started "
        f"(scan every {settings.lup_proactive_scan_interval_minutes}m, "
        f"flush every {settings.lup_batch_flush_interval_minutes}m). Press Ctrl+C to exit."
    )
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        scheduler.shutdown(wait=False)
        print("LUP Scheduler stopped.")


if __name__ == "__main__":
    main()
