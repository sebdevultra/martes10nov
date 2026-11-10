"""Unit tests for LocalCsvAnalyticsRepository and QueryCatalog caching."""

from datetime import date
from pathlib import Path

import pytest

from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import QueryCatalog
from lup_ai.data.sql_guard import SQLGuardError
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_local_repo_mirrors_all_five_semantic_views_without_pii(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "local.db"
    generate_dataset(output_dir=data_dir, days=30, seed=7, sqlite_path=db_path)

    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    store = LocalStore(db_path)
    catalog = QueryCatalog(repo=repo, store=store, cache_ttl_seconds=3600)

    start, end = date(2026, 10, 12), date(2026, 11, 10)
    fin = catalog.query_finance(start, end)
    assert fin.cache_hit is False
    assert fin.aggregates["total_spend"] > 0
    assert fin.rows
    assert not ({"merchant_raw", "account_alias", "notes_raw"} & set(fin.rows[0].keys()))

    # Second call hits local TTL cache.
    fin_cached = catalog.query_finance(start, end)
    assert fin_cached.cache_hit is True

    hab = catalog.query_habits_sleep(start, end)
    assert hab.aggregates["row_count"] == 30
    assert "notes_raw" not in hab.rows[0]

    tim = catalog.query_time_calendar(start, end)
    assert tim.aggregates["total_completed_hours"] > 0
    assert "title_raw" not in tim.rows[0]

    cross = catalog.query_cross_domain(start, end)
    assert cross.aggregates["row_count"] == 30

    goals = catalog.query_goals()
    assert goals.aggregates["active_goals_count"] == 4


def test_local_repo_executes_guarded_semantic_sql_and_blocks_raw(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    generate_dataset(output_dir=data_dir, days=21, seed=9)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)

    rows = repo.execute_semantic_sql(
        "SELECT direction, SUM(total_amount) AS amt "
        "FROM LUP_AI_DB.SEMANTIC.VW_FINANCE_SUMMARY GROUP BY direction"
    )
    assert len(rows) >= 2

    with pytest.raises(SQLGuardError):
        repo.execute_semantic_sql("SELECT * FROM RAW.RAW_FINANCE_TX")
