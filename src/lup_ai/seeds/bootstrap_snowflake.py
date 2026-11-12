"""Bootstrap Snowflake LUP_AI_DB DDLs, stage CSV upload, and initial seed verification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lup_ai.config.settings import Settings
from lup_ai.data.snowflake_client import (
    build_snowflake_connect_kwargs,
    has_valid_snowflake_credentials,
)
from lup_ai.seeds.generate_synthetic_data import generate_dataset

_DDL_FILES: tuple[str, ...] = (
    "01_databases_warehouses_roles.sql",
    "02_raw_tables.sql",
    "03_core_models.sql",
    "04_analytics_dynamic_tables.sql",
    "05_semantic_views.sql",
)


@dataclass(frozen=True)
class BootstrapReport:
    """Result summary produced by bootstrap_snowflake."""

    ddl_scripts_executed: int
    files_uploaded: list[str]
    cross_domain_rows: int
    goals_rows: int
    dry_run: bool


def _default_connector_factory(settings: Settings) -> Any:
    import snowflake.connector

    kwargs = build_snowflake_connect_kwargs(settings)
    return snowflake.connector.connect(**kwargs)


def bootstrap_snowflake(
    settings: Settings,
    project_root: Path = Path("."),
    data_dir: Path = Path("data/generated"),
    dry_run: bool = False,
    connector_factory: Callable[[Settings], Any] | None = None,
) -> BootstrapReport:
    """Provision DDLs 01..05, upload synthetic CSVs, run COPY INTO, and verify SEMANTIC views."""
    ddl_dir = project_root / "snowflake" / "ddl"
    seed_sql_path = project_root / "snowflake" / "seeds" / "load_synthetic_data.sql"

    for fname in _DDL_FILES:
        if not (ddl_dir / fname).exists():
            raise FileNotFoundError(f"Missing DDL script: {ddl_dir / fname}")
    if not seed_sql_path.exists():
        raise FileNotFoundError(f"Missing seed SQL script: {seed_sql_path}")

    required_csvs = {
        "finance": data_dir / "finance_tx.csv",
        "habits": data_dir / "habits_sleep.csv",
        "time": data_dir / "time_calendar.csv",
    }
    if not all(path.exists() for path in required_csvs.values()):
        generate_dataset(
            output_dir=data_dir, days=180, seed=42, sqlite_path=settings.lup_sqlite_path
        )

    if dry_run:
        return BootstrapReport(
            ddl_scripts_executed=len(_DDL_FILES),
            files_uploaded=[str(p) for p in required_csvs.values()],
            cross_domain_rows=0,
            goals_rows=0,
            dry_run=True,
        )

    if not has_valid_snowflake_credentials(settings):
        raise RuntimeError("Snowflake credentials in .env are missing or still placeholders.")

    connect_fn = connector_factory or _default_connector_factory
    conn = connect_fn(settings)
    uploaded: list[str] = []
    try:
        for fname in _DDL_FILES:
            sql_text = (ddl_dir / fname).read_text(encoding="utf-8")
            conn.execute_string(sql_text, remove_comments=True)

        with conn.cursor() as cur:
            cur.execute("USE ROLE LUP_INGEST_RW")
            cur.execute("USE WAREHOUSE LUP_WH_XS")
            cur.execute("USE DATABASE LUP_AI_DB")
            for stage_sub, csv_path in required_csvs.items():
                abs_posix = csv_path.resolve().as_posix()
                cur.execute(
                    f"PUT 'file://{abs_posix}' @LUP_AI_DB.RAW.LUP_INTERNAL_STAGE/{stage_sub}/ "
                    "AUTO_COMPRESS=TRUE OVERWRITE=TRUE"
                )
                uploaded.append(str(csv_path))

        seed_sql = seed_sql_path.read_text(encoding="utf-8")
        conn.execute_string(seed_sql, remove_comments=True)

        # Trigger immediate refresh of Dynamic Tables so SEMANTIC views are populated right away.
        with conn.cursor() as cur:
            cur.execute("USE ROLE LUP_INGEST_RW")
            cur.execute("USE WAREHOUSE LUP_WH_XS")
            cur.execute("ALTER DYNAMIC TABLE LUP_AI_DB.ANALYTICS.FCT_DAILY_SNAPSHOT REFRESH")
            cur.execute("ALTER DYNAMIC TABLE LUP_AI_DB.ANALYTICS.FCT_WEEKLY_TRENDS REFRESH")

            cur.execute("USE ROLE LUP_AGENT_RO")
            cur.execute("SELECT COUNT(*) FROM LUP_AI_DB.SEMANTIC.VW_CROSS_DOMAIN_DAILY")
            cross_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM LUP_AI_DB.SEMANTIC.VW_GOAL_PROGRESS")
            goals_count = int(cur.fetchone()[0])

        return BootstrapReport(
            ddl_scripts_executed=len(_DDL_FILES),
            files_uploaded=uploaded,
            cross_domain_rows=cross_count,
            goals_rows=goals_count,
            dry_run=False,
        )
    finally:
        conn.close()
