"""Unit tests for SQLite LocalStore."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

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
