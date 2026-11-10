"""Level-1 parameterized query catalog across Finance, Habits/Sleep, and Time/Calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from lup_ai.data.local_store import LocalStore
from lup_ai.data.repository import AnalyticsRepository

DomainLiteral = Literal["finance", "habits_sleep", "time_calendar", "cross_domain", "goals"]


@dataclass(frozen=True)
class CatalogResult:
    """Typed response returned by QueryCatalog with aggregated summary metrics."""

    domain: DomainLiteral
    source_view: str
    backend: str
    cache_hit: bool
    start_date: date | None
    end_date: date | None
    rows: list[dict[str, Any]]
    aggregates: dict[str, Any]


def _summarize_finance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_income = sum(r["total_amount"] for r in rows if r["direction"] == "income")
    total_spend = sum(r["total_amount"] for r in rows if r["direction"] == "expense")
    discretionary = sum(
        r["total_amount"] for r in rows if r["direction"] == "expense" and r["is_discretionary"]
    )
    savings = sum(r["total_amount"] for r in rows if r["direction"] == "saving")

    by_category: dict[str, float] = {}
    for row in rows:
        if row["direction"] == "expense":
            cat = str(row["category"])
            by_category[cat] = round(by_category.get(cat, 0.0) + float(row["total_amount"]), 2)

    return {
        "row_count": len(rows),
        "total_income": round(total_income, 2),
        "total_spend": round(total_spend, 2),
        "discretionary_spend": round(discretionary, 2),
        "allocated_savings": round(savings, 2),
        "net_cashflow": round(total_income - total_spend, 2),
        "spend_by_category": dict(sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)),
    }


def _summarize_habits(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"row_count": 0}
    n = len(rows)
    avg_sleep = sum(float(r["sleep_hours"]) for r in rows) / n
    avg_quality = sum(float(r["sleep_quality_score"] or 0) for r in rows) / n
    avg_habit = sum(float(r["habit_completion_rate"] or 0) for r in rows) / n
    avg_energy = sum(float(r["energy_score"] or 0) for r in rows) / n
    total_exercise = sum(int(r["exercise_minutes"] or 0) for r in rows)
    return {
        "row_count": n,
        "avg_sleep_hours": round(avg_sleep, 2),
        "avg_sleep_quality_score": round(avg_quality, 2),
        "avg_habit_completion_rate": round(avg_habit, 4),
        "avg_energy_score": round(avg_energy, 2),
        "total_exercise_minutes": total_exercise,
    }


def _summarize_time(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [r for r in rows if r["completed_flag"]]
    by_block: dict[str, float] = {}
    for row in completed:
        btype = str(row["block_type"])
        by_block[btype] = round(by_block.get(btype, 0.0) + float(row["total_hours"]), 2)
    return {
        "row_count": len(rows),
        "total_completed_hours": round(sum(by_block.values()), 2),
        "hours_by_block_type": dict(sorted(by_block.items(), key=lambda kv: kv[1], reverse=True)),
    }


def _summarize_cross_domain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"row_count": 0}
    n = len(rows)
    sleep_vals = [float(r["sleep_hours"]) for r in rows if r.get("sleep_hours") is not None]
    avg_sleep = sum(sleep_vals) / len(sleep_vals) if sleep_vals else 0.0
    return {
        "row_count": n,
        "total_income": round(sum(float(r["total_income"] or 0) for r in rows), 2),
        "total_spend": round(sum(float(r["total_spend"] or 0) for r in rows), 2),
        "discretionary_spend": round(sum(float(r["discretionary_spend"] or 0) for r in rows), 2),
        "allocated_savings": round(sum(float(r["allocated_savings"] or 0) for r in rows), 2),
        "avg_sleep_hours": round(avg_sleep, 2),
        "total_deep_work_hours": round(sum(float(r["deep_work_hours"] or 0) for r in rows), 2),
        "total_meeting_hours": round(sum(float(r["meeting_hours"] or 0) for r in rows), 2),
    }


class QueryCatalog:
    """Parameterized Level-1 query dispatcher with SQLite TTL caching."""

    def __init__(
        self,
        repo: AnalyticsRepository,
        store: LocalStore,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self._repo = repo
        self._store = store
        self._ttl = cache_ttl_seconds

    @staticmethod
    def default_range(days: int = 30, anchor_date: date | None = None) -> tuple[date, date]:
        """Compute inclusive [start_date, end_date] range."""
        end = anchor_date or date(2026, 11, 10)
        start = end - timedelta(days=max(1, days) - 1)
        return start, end

    def _cached_fetch(
        self,
        cache_key: str,
        loader: Any,
    ) -> tuple[list[dict[str, Any]], bool]:
        cached = self._store.get_cache(cache_key)
        if cached is not None:
            return cached, True
        rows = loader()
        self._store.set_cache(cache_key, rows, ttl_seconds=self._ttl)
        return rows, False

    def query_finance(
        self,
        start_date: date,
        end_date: date,
        direction: str | None = None,
        category: str | None = None,
        discretionary_only: bool = False,
    ) -> CatalogResult:
        """Query aggregated finance metrics over VW_FINANCE_SUMMARY."""
        key = (
            f"cat:{self._repo.backend_name}:finance:{start_date}:{end_date}:"
            f"{direction}:{category}:{discretionary_only}"
        )
        rows, hit = self._cached_fetch(
            key,
            lambda: self._repo.fetch_finance_summary(
                start_date,
                end_date,
                direction=direction,
                category=category,
                discretionary_only=discretionary_only,
            ),
        )
        return CatalogResult(
            domain="finance",
            source_view="SEMANTIC.VW_FINANCE_SUMMARY",
            backend=self._repo.backend_name,
            cache_hit=hit,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            aggregates=_summarize_finance(rows),
        )

    def query_habits_sleep(self, start_date: date, end_date: date) -> CatalogResult:
        """Query daily habits and sleep metrics over VW_HABITS_SLEEP_SUMMARY."""
        key = f"cat:{self._repo.backend_name}:habits_sleep:{start_date}:{end_date}"
        rows, hit = self._cached_fetch(
            key, lambda: self._repo.fetch_habits_sleep_summary(start_date, end_date)
        )
        return CatalogResult(
            domain="habits_sleep",
            source_view="SEMANTIC.VW_HABITS_SLEEP_SUMMARY",
            backend=self._repo.backend_name,
            cache_hit=hit,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            aggregates=_summarize_habits(rows),
        )

    def query_time_calendar(
        self,
        start_date: date,
        end_date: date,
        domain_tag: str | None = None,
        block_type: str | None = None,
    ) -> CatalogResult:
        """Query daily calendar time allocation over VW_TIME_CALENDAR_SUMMARY."""
        key = (
            f"cat:{self._repo.backend_name}:time_calendar:{start_date}:{end_date}:"
            f"{domain_tag}:{block_type}"
        )
        rows, hit = self._cached_fetch(
            key,
            lambda: self._repo.fetch_time_calendar_summary(
                start_date, end_date, domain_tag=domain_tag, block_type=block_type
            ),
        )
        return CatalogResult(
            domain="time_calendar",
            source_view="SEMANTIC.VW_TIME_CALENDAR_SUMMARY",
            backend=self._repo.backend_name,
            cache_hit=hit,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            aggregates=_summarize_time(rows),
        )

    def query_cross_domain(self, start_date: date, end_date: date) -> CatalogResult:
        """Query unified cross-domain metrics over VW_CROSS_DOMAIN_DAILY."""
        key = f"cat:{self._repo.backend_name}:cross_domain:{start_date}:{end_date}"
        rows, hit = self._cached_fetch(
            key, lambda: self._repo.fetch_cross_domain_daily(start_date, end_date)
        )
        return CatalogResult(
            domain="cross_domain",
            source_view="SEMANTIC.VW_CROSS_DOMAIN_DAILY",
            backend=self._repo.backend_name,
            cache_hit=hit,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            aggregates=_summarize_cross_domain(rows),
        )

    def query_goals(self) -> CatalogResult:
        """Query active goal progress over VW_GOAL_PROGRESS."""
        key = f"cat:{self._repo.backend_name}:goals"
        rows, hit = self._cached_fetch(key, self._repo.fetch_goal_progress)
        return CatalogResult(
            domain="goals",
            source_view="SEMANTIC.VW_GOAL_PROGRESS",
            backend=self._repo.backend_name,
            cache_hit=hit,
            start_date=None,
            end_date=None,
            rows=rows,
            aggregates={"active_goals_count": len(rows)},
        )
