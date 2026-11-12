"""Read-only Snowflake analytics repository using LUP_AGENT_RO and SQL Guard."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from lup_ai.config.settings import Settings
from lup_ai.data.sql_guard import validate_and_prepare_sql

_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "your_account_locator",
        "your_snowflake_user",
        "your_snowflake_password",
    }
)


class SnowflakeConfigurationError(RuntimeError):
    """Raised when Snowflake connection parameters are incomplete."""


def has_valid_snowflake_credentials(settings: Settings) -> bool:
    """Return True when non-placeholder Snowflake credentials are configured."""
    account = settings.snowflake_account.strip()
    user = settings.snowflake_user.strip()
    password = settings.snowflake_password.strip()
    key_path = settings.snowflake_private_key_path.strip()

    if account in _PLACEHOLDER_VALUES or user in _PLACEHOLDER_VALUES:
        return False
    has_password = password not in _PLACEHOLDER_VALUES
    has_key = bool(key_path)
    return has_password or has_key


def build_snowflake_connect_kwargs(
    settings: Settings,
    role: str | None = None,
    schema: str | None = None,
    database: str | None = None,
    warehouse: str | None = None,
) -> dict[str, Any]:
    """Build snowflake.connector.connect keyword arguments supporting password or PKCS8 key."""
    if not has_valid_snowflake_credentials(settings):
        raise SnowflakeConfigurationError("Snowflake credentials are not configured.")

    conn_kwargs: dict[str, Any] = {
        "account": settings.snowflake_account.strip(),
        "user": settings.snowflake_user.strip(),
        "session_parameters": {
            "STATEMENT_TIMEOUT_IN_SECONDS": settings.snowflake_statement_timeout_seconds,
            "TIMEZONE": settings.lup_timezone,
        },
    }
    if role is not None:
        conn_kwargs["role"] = role
    if warehouse is not None:
        conn_kwargs["warehouse"] = warehouse
    if database is not None:
        conn_kwargs["database"] = database
    if schema is not None:
        conn_kwargs["schema"] = schema

    key_path = settings.snowflake_private_key_path.strip()
    if key_path:
        resolved_key_path = Path(key_path).expanduser()
        if not resolved_key_path.exists():
            raise SnowflakeConfigurationError(
                f"Snowflake private key file not found: {resolved_key_path}"
            )
        conn_kwargs["authenticator"] = "SNOWFLAKE_JWT"
        conn_kwargs["private_key_file"] = str(resolved_key_path)
        if settings.snowflake_private_key_passphrase:
            conn_kwargs["private_key_password"] = settings.snowflake_private_key_passphrase
    else:
        conn_kwargs["password"] = settings.snowflake_password.strip()

    return conn_kwargs


class SnowflakeAnalyticsRepository:
    """Execute validated read-only queries against Snowflake SEMANTIC views."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def backend_name(self) -> str:
        return "snowflake"

    def is_configured(self) -> bool:
        """Return True when required Snowflake credentials are present."""
        return has_valid_snowflake_credentials(self._settings)

    def _connect(self) -> Any:
        import snowflake.connector

        kwargs = build_snowflake_connect_kwargs(
            self._settings,
            role=self._settings.snowflake_role_agent,
            warehouse=self._settings.snowflake_warehouse,
            database=self._settings.snowflake_database,
            schema=self._settings.snowflake_schema_semantic,
        )
        return snowflake.connector.connect(**kwargs)

    @staticmethod
    def _normalize_cell(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value

    def _run_query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        safe_sql = validate_and_prepare_sql(
            sql,
            allowed_database=self._settings.snowflake_database,
            allowed_schema=self._settings.snowflake_schema_semantic,
            allow_pyformat_params=bool(params),
        )
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(safe_sql, params or ())
                columns = [col[0].lower() for col in cursor.description]
                return [
                    {
                        col: self._normalize_cell(val)
                        for col, val in zip(columns, row, strict=False)
                    }
                    for row in cursor.fetchall()
                ]
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
