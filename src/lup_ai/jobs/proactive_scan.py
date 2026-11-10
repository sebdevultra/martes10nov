"""Proactive decision scanner detecting anomalies, opportunities, and matured outcomes."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lup_ai.config.settings import get_settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.engines.anomaly_detector import AnomalyDetector
from lup_ai.engines.recommendation_builder import RecommendationBuilder, RecommendationPayload
from lup_ai.memory.decision_tracker import DecisionOutcomeRecord, DecisionTracker


class ProactiveScanReport(BaseModel):
    """Summary returned by ProactiveScanner after scanning anomalies and outcomes."""

    scan_date: str
    lookback_days: int
    data_backend: str
    anomalies_found: int
    opportunities_found: int
    persisted_decision_ids: list[str] = Field(default_factory=list)
    recommendations: list[RecommendationPayload] = Field(default_factory=list)
    evaluated_outcomes: list[DecisionOutcomeRecord] = Field(default_factory=list)


class ProactiveScanner:
    """Scan multi-domain metrics proactively to populate the decision inbox and outcomes."""

    def __init__(
        self,
        store: LocalStore,
        repo: AnalyticsRepository,
        anchor_date: date | None = None,
    ) -> None:
        self._store = store
        self._repo = repo
        self._detector = AnomalyDetector()
        self._rec_builder = RecommendationBuilder()
        self._tracker = DecisionTracker(store)
        self._anchor_date = anchor_date

    def _load_goals_and_prefs(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            goals = self._repo.fetch_goal_progress()
        except FileNotFoundError:
            goals = []
        if not goals:
            goals = self._store.list_active_goals()
        return goals, self._store.list_preferences()

    def run(
        self,
        lookback_days: int = 90,
        evaluate_outcomes_force: bool = False,
    ) -> ProactiveScanReport:
        """Execute deterministic anomaly/opportunity scan and Decision Outcome Loop evaluation."""
        end_date = self._anchor_date or date(2026, 11, 10)
        start_date = end_date - timedelta(days=max(14, lookback_days) - 1)
        daily_rows = self._repo.fetch_cross_domain_daily(start_date=start_date, end_date=end_date)
        goals, prefs = self._load_goals_and_prefs()

        scan = self._detector.scan(daily_rows, preferences=prefs, goals=goals)
        recommendations = self._rec_builder.from_anomaly_scan(scan)
        decision_ids = self._tracker.persist_recommendations(
            recommendations=recommendations,
            source_type="proactive_scan",
            model_used="deterministic_proactive_scan",
            prompt_summary=f"Proactive scan ({start_date} a {end_date})",
        )
        outcomes = self._tracker.evaluate_pending_outcomes(
            repo=self._repo,
            anchor_date=end_date,
            force=evaluate_outcomes_force,
        )

        return ProactiveScanReport(
            scan_date=end_date.isoformat(),
            lookback_days=lookback_days,
            data_backend=self._repo.backend_name,
            anomalies_found=len(scan.anomalies),
            opportunities_found=len(scan.opportunities),
            persisted_decision_ids=decision_ids,
            recommendations=recommendations,
            evaluated_outcomes=outcomes,
        )


def main() -> None:
    """CLI entry point for scheduled cron/task execution of proactive scans."""
    parser = argparse.ArgumentParser(description="Run LUP AI proactive anomaly & outcome scan.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--data-dir", type=Path, default=Path("data/generated"))
    args = parser.parse_args()

    settings = get_settings()
    store = LocalStore(settings.lup_sqlite_path)
    sf_repo = SnowflakeAnalyticsRepository(settings)
    repo: AnalyticsRepository = (
        sf_repo if sf_repo.is_configured() else LocalCsvAnalyticsRepository(args.data_dir)
    )
    report = ProactiveScanner(store=store, repo=repo).run(lookback_days=args.days)
    print(
        f"Scan {report.scan_date}: anomalies={report.anomalies_found}, "
        f"opportunities={report.opportunities_found}, "
        f"new_decisions={len(report.persisted_decision_ids)}, "
        f"outcomes={len(report.evaluated_outcomes)}"
    )


if __name__ == "__main__":
    main()
