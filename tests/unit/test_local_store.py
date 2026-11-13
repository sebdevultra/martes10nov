"""Unit tests for SQLite LocalStore."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lup_ai.data.local_store import LocalStore


def test_cache_roundtrip_and_expiration(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "local.db")
    store.set_cache("kpis:weekly", {"total_spend": 120000.0}, ttl_seconds=120)
    assert store.get_cache("kpis:weekly") == {"total_spend": 120000.0}

    # Force expiration in SQLite to verify TTL eviction.
    expired_ts = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE metric_cache SET expires_at = ? WHERE cache_key = ?",
            (expired_ts, "kpis:weekly"),
        )
    assert store.get_cache("kpis:weekly") is None


def test_goals_preferences_and_pii_aliases(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "local.db")
    store.upsert_goal(
        {
            "goal_id": "g1",
            "domain": "finance",
            "title": "Meta ahorro",
            "metric_name": "weekly_allocated_savings",
            "target_value": 300000,
            "unit": "COP",
            "priority": 1,
        }
    )
    goals = store.list_active_goals()
    assert len(goals) == 1
    assert goals[0]["goal_id"] == "g1"

    store.upsert_preference(
        {
            "pref_id": "p1",
            "domain": "habits_sleep",
            "key_name": "min_sleep",
            "value_json": {"min_hours": 6.5},
            "is_hard_constraint": True,
        }
    )
    prefs = store.list_preferences()
    assert prefs[0]["is_hard_constraint"] is True

    store.store_alias("sess-1", "[COMERCIO_1]", "ORG", "Supermercado Demo")
    assert store.get_session_aliases("sess-1") == {"[COMERCIO_1]": "Supermercado Demo"}
    store.clear_session_aliases("sess-1")
    assert store.get_session_aliases("sess-1") == {}


def test_goal_and_preference_crud_and_snowflake_sync(tmp_path: Path) -> None:
    class _FakeCursor:
        def __init__(self, queries: list[tuple[str, Any]]) -> None:
            self.queries = queries

        def execute(self, sql: str, params: Any = None) -> None:
            self.queries.append((sql, params))

        def __enter__(self) -> "_FakeCursor":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    class _FakeConn:
        def __init__(self) -> None:
            self.queries: list[tuple[str, Any]] = []
            self.closed = False

        def cursor(self) -> _FakeCursor:
            return _FakeCursor(self.queries)

        def close(self) -> None:
            self.closed = True

    fake_conn = _FakeConn()
    store = LocalStore(
        tmp_path / "local.db",
        snowflake_conn_factory=lambda _cfg: fake_conn,
    )

    # 1. Goal CRUD
    created_g = store.create_goal(
        {
            "domain": "finance",
            "title": "Ahorro anual",
            "metric_name": "net_savings",
            "target_value": 5000000.0,
            "unit": "COP",
            "priority": 1,
            "comparison_operator": ">=",
            "status": "active",
        }
    )
    gid = created_g["goal_id"]
    assert gid.startswith("goal_finance_")
    assert store.get_goal(gid)["title"] == "Ahorro anual"
    assert any("MERGE INTO ANALYTICS.DIM_GOALS" in q[0] for q in fake_conn.queries)

    updated_g = store.update_goal(gid, {"target_value": 6000000.0, "title": "Ahorro actualizado"})
    assert updated_g is not None
    assert updated_g["target_value"] == 6000000.0
    assert updated_g["title"] == "Ahorro actualizado"

    deleted = store.delete_goal(gid)
    assert deleted is True
    assert store.get_goal(gid) is None
    assert any("DELETE FROM ANALYTICS.DIM_GOALS" in q[0] for q in fake_conn.queries)
    assert store.delete_goal("nonexistent") is False

    # 2. Preference CRUD
    fake_conn.queries.clear()
    created_p = store.create_preference(
        {
            "domain": "habits_sleep",
            "key_name": "target_sleep",
            "value_json": {"target_hours": 8.0},
            "is_hard_constraint": True,
        }
    )
    pid = created_p["pref_id"]
    assert pid == "pref_habits_sleep_target_sleep"
    assert created_p["is_hard_constraint"] is True
    assert store.get_preference(pid) is not None
    assert any("MERGE INTO ANALYTICS.DIM_PREFERENCES" in q[0] for q in fake_conn.queries)

    updated_p = store.update_preference(pid, {"is_hard_constraint": False})
    assert updated_p is not None
    assert updated_p["is_hard_constraint"] is False

    deleted_p = store.delete_preference(pid)
    assert deleted_p is True
    assert store.get_preference(pid) is None
    assert any("DELETE FROM ANALYTICS.DIM_PREFERENCES" in q[0] for q in fake_conn.queries)
    assert store.delete_preference("nonexistent") is False
