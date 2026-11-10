"""Read-only Snowflake analytics repository using LUP_AGENT_RO and SQL Guard."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from lup_ai.config.settings import Settings
from lup_ai.data.sql_guard import validate_and_prepare_sql


class SnowflakeConfigurationError(RuntimeError):
    """Raised when Snowflake connection parameters are incomplete."""


class SnowflakeAnalyticsRepository:
    """Execute validated read-only queries against Snowflake SEMANTIC views."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def backend_name(self) -> str:
        return "snowflake"

    def is_configured(self) -> bool:
        """Return True when required Snowflake credentials are present."""
        has_auth = bool(
            self._settings.snowflake_password or self._settings.snowflake_private_key_path
        )
        return bool(self._settings.snowflake_account and self._settings.snowflake_user and has_auth)

    def _connect(self) -> Any:
        if not self.is_configured():
            raise SnowflakeConfigurationError("Snowflake credentials are not configured.")

        import snowflake.connector

        conn_kwargs: dict[str, Any] = {
            "account": self._settings.snowflake_account,
            "user": self._settings.snowflake_user,
            "role": self._settings.snowflake_role_agent,
            "warehouse": self._settings.snowflake_warehouse,
            "database": self._settings.snowflake_database,
            "schema": self._settings.snowflake_schema_semantic,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": self._settings.snowflake_statement_timeout_seconds,
                "TIMEZONE": self._settings.lup_timezone,
            },
        }
        if self._settings.snowflake_private_key_path:
            from cryptography.hazmat.primitives import serialization

            key_bytes = Path(self._settings.snowflake_private_key_path).read_bytes()
            passphrase = (
                self._settings.snowflake_private_key_passphrase.encode("utf-8")
                if self._settings.snowflake_private_key_passphrase
                else None
            )
            private_key = serialization.load_pem_private_key(key_bytes, password=passphrase)
            conn_kwargs["private_key"] = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        else:
            conn_kwargs["password"] = self._settings.snowflake_password

        return snowflake.connector.connect(**conn_kwargs)

    def _run_query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        safe_sql = validate_and_prepare_sql(
            sql,
            allowed_database=self._settings.snowflake_database,
            allowed_schema=self._settings.snowflake_schema_semantic,
        )
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(safe_sql, params or ())
                columns = [col[0].lower() for col in cursor.description]
                return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def execute_semantic_sql(self, sql: str) -> list[dict[str, Any]]:
        """Validate and execute a read-only SELECT query against SEMANTIC views."""
        return self._run_query(sql)

    def fetch_finance_summary(
        self,
        start_date: date,
        end_date: date,
        direction: str | None = None,
        category: str | None = None,
        discretionary_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["tx_date BETWEEN %s AND %s"]
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]
        if direction:
            clauses.append("direction = %s")
            params.append(direction.lower())
        if category:
            clauses.append("category = %s")
            params.append(category.upper())
        if discretionary_only:
            clauses.append("is_discretionary = TRUE")
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT tx_date, week_start, month_start, currency, direction, "
            "category, is_discretionary, tx_count, total_amount, avg_amount "
            "FROM SEMANTIC.VW_FINANCE_SUMMARY "
            f"WHERE {where_sql} "
            "ORDER BY tx_date DESC"
        )
        return self._run_query(sql, tuple(params))

    def fetch_habits_sleep_summary(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT log_date, week_start, sleep_hours, sleep_quality_score, "
            "exercise_minutes, habit_completed_count, habit_total_count, "
            "habit_completion_rate, energy_score "
            "FROM SEMANTIC.VW_HABITS_SLEEP_SUMMARY "
            "WHERE log_date BETWEEN %s AND %s "
            "ORDER BY log_date DESC"
        )
        return self._run_query(sql, (start_date.isoformat(), end_date.isoformat()))

    def fetch_time_calendar_summary(
        self,
        start_date: date,
        end_date: date,
        domain_tag: str | None = None,
        block_type: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["event_date BETWEEN %s AND %s"]
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]
        if domain_tag:
            clauses.append("domain_tag = %s")
            params.append(domain_tag.upper())
        if block_type:
            clauses.append("block_type = %s")
            params.append(block_type.upper())
        where_sql = " AND ".join(clauses)
        sql = (
            "SELECT event_date, week_start, domain_tag, block_type, priority, "
            "completed_flag, block_count, total_minutes, total_hours "
            "FROM SEMANTIC.VW_TIME_CALENDAR_SUMMARY "
            f"WHERE {where_sql} "
            "ORDER BY event_date DESC"
        )
        return self._run_query(sql, tuple(params))

    def fetch_cross_domain_daily(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT snapshot_date, week_start, day_of_week, total_income, total_spend, "
            "discretionary_spend, net_cashflow, allocated_savings, sleep_hours, "
            "sleep_quality_score, exercise_minutes, habit_completion_rate, energy_score, "
            "deep_work_hours, meeting_hours, leisure_hours, total_scheduled_hours "
            "FROM SEMANTIC.VW_CROSS_DOMAIN_DAILY "
            "WHERE snapshot_date BETWEEN %s AND %s "
            "ORDER BY snapshot_date DESC"
        )
        return self._run_query(sql, (start_date.isoformat(), end_date.isoformat()))

    def fetch_goal_progress(self) -> list[dict[str, Any]]:
        sql = (
            "SELECT goal_id, domain, title, metric_name, target_value, unit, "
            "comparison_operator, deadline, priority, status, latest_week, latest_observed_value "
            "FROM SEMANTIC.VW_GOAL_PROGRESS "
            "ORDER BY priority ASC, goal_id ASC"
        )
        return self._run_query(sql)
