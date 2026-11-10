"""Shared analytics repository protocol and domain query filters."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class AnalyticsRepository(Protocol):
    """Unified read-only interface over SEMANTIC views in Snowflake or local synthetic CSVs."""

    @property
    def backend_name(self) -> str:
        """Return repository backend identifier."""

    def execute_semantic_sql(self, sql: str) -> list[dict[str, Any]]:
        """Validate and execute a read-only SELECT query against SEMANTIC views."""

    def fetch_finance_summary(
        self,
        start_date: date,
        end_date: date,
        direction: str | None = None,
        category: str | None = None,
        discretionary_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return daily and category finance rows from VW_FINANCE_SUMMARY."""

    def fetch_habits_sleep_summary(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Return daily habits and sleep rows from VW_HABITS_SLEEP_SUMMARY."""

    def fetch_time_calendar_summary(
        self,
        start_date: date,
        end_date: date,
        domain_tag: str | None = None,
        block_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily time allocation rows from VW_TIME_CALENDAR_SUMMARY."""

    def fetch_cross_domain_daily(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Return unified daily snapshot rows from VW_CROSS_DOMAIN_DAILY."""

    def fetch_goal_progress(self) -> list[dict[str, Any]]:
        """Return active goals joined with latest weekly observed metric."""
