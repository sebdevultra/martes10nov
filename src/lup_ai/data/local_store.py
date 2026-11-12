"""Local SQLite store for OLTP state, TTL metric cache, goals mirror, and PII alias map."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class LocalStore:
    """Manage local SQLite persistence to avoid OLTP load and unnecessary Snowflake wake-ups."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create local SQLite schema idempotently."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model_used TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metric_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS goals_mirror (
                    goal_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    target_value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    comparison_operator TEXT NOT NULL DEFAULT '>=',
                    deadline TEXT,
                    priority INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS preferences_mirror (
                    pref_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    key_name TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    is_hard_constraint INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pii_alias_map (
                    session_id TEXT NOT NULL,
                    alias_token TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    original_value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, alias_token)
                );

                CREATE TABLE IF NOT EXISTS decisions_log (
                    decision_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    prompt_summary TEXT NOT NULL DEFAULT '',
                    recommendation_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    privacy_mode TEXT NOT NULL DEFAULT 'masked_cloud',
                    source_type TEXT NOT NULL DEFAULT 'interactive',
                    metric_name TEXT NOT NULL DEFAULT '',
                    baseline_value REAL,
                    user_action TEXT NOT NULL DEFAULT 'pending',
                    user_feedback TEXT,
                    actioned_at TEXT,
                    evaluate_after_days INTEGER NOT NULL DEFAULT 7,
                    synced_to_snowflake INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    baseline_value REAL,
                    observed_value REAL,
                    delta_impact REAL NOT NULL,
                    outcome_status TEXT NOT NULL,
                    user_feedback TEXT,
                    synced_to_snowflake INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS ingest_buffer (
                    entry_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    synced_at TEXT,
                    sync_status TEXT NOT NULL DEFAULT 'pending'
                );
                """
            )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)

    def set_cache(self, cache_key: str, payload: Any, ttl_seconds: int = 3600) -> None:
        """Store JSON-serializable payload in TTL cache."""
        now = self._utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        serialized = json.dumps(payload, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metric_cache (cache_key, payload_json, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (cache_key, serialized, expires.isoformat(), now.isoformat()),
            )

    def get_cache(self, cache_key: str) -> Any | None:
        """Retrieve cached payload if present and not expired."""
        now_iso = self._utc_now().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM metric_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now_iso:
                conn.execute("DELETE FROM metric_cache WHERE cache_key = ?", (cache_key,))
                return None
            return json.loads(row["payload_json"])

    def upsert_goal(self, goal: dict[str, Any]) -> None:
        """Insert or update a goal in the local mirror."""
        now_iso = self._utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goals_mirror (
                    goal_id, domain, title, metric_name, target_value,
                    unit, comparison_operator, deadline, priority, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                    domain = excluded.domain,
                    title = excluded.title,
                    metric_name = excluded.metric_name,
                    target_value = excluded.target_value,
                    unit = excluded.unit,
                    comparison_operator = excluded.comparison_operator,
                    deadline = excluded.deadline,
                    priority = excluded.priority,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    goal["goal_id"],
                    goal["domain"],
                    goal["title"],
                    goal["metric_name"],
                    float(goal["target_value"]),
                    goal["unit"],
                    goal.get("comparison_operator", ">="),
                    goal.get("deadline"),
                    int(goal.get("priority", 1)),
                    goal.get("status", "active"),
                    now_iso,
                ),
            )

    def list_active_goals(self) -> list[dict[str, Any]]:
        """Return active goals ordered by priority."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT goal_id, domain, title, metric_name, target_value,
                       unit, comparison_operator, deadline, priority, status
                FROM goals_mirror
                WHERE status = 'active'
                ORDER BY priority ASC, goal_id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_preference(self, pref: dict[str, Any]) -> None:
        """Insert or update a user preference or hard constraint."""
        now_iso = self._utc_now().isoformat()
        value_json = (
            pref["value_json"]
            if isinstance(pref["value_json"], str)
            else json.dumps(pref["value_json"])
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO preferences_mirror (
                    pref_id, domain, key_name, value_json, is_hard_constraint, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pref_id) DO UPDATE SET
                    domain = excluded.domain,
                    key_name = excluded.key_name,
                    value_json = excluded.value_json,
                    is_hard_constraint = excluded.is_hard_constraint,
                    updated_at = excluded.updated_at
                """,
                (
                    pref["pref_id"],
                    pref["domain"],
                    pref["key_name"],
                    value_json,
                    1 if pref.get("is_hard_constraint", False) else 0,
                    now_iso,
                ),
            )

    def list_preferences(self) -> list[dict[str, Any]]:
        """Return all stored user preferences and hard constraints."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pref_id, domain, key_name, value_json, is_hard_constraint
                FROM preferences_mirror
                ORDER BY is_hard_constraint DESC, pref_id ASC
                """
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["is_hard_constraint"] = bool(item["is_hard_constraint"])
                result.append(item)
            return result

    def store_alias(
        self,
        session_id: str,
        alias_token: str,
        entity_type: str,
        original_value: str,
    ) -> None:
        """Persist local reversible alias for PII anonymization."""
        now_iso = self._utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pii_alias_map (
                    session_id, alias_token, entity_type, original_value, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id, alias_token) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    original_value = excluded.original_value
                """,
                (session_id, alias_token, entity_type, original_value, now_iso),
            )

    def get_session_aliases(self, session_id: str) -> dict[str, str]:
        """Return alias-to-original map for local re-hydration."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT alias_token, original_value FROM pii_alias_map WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            return {row["alias_token"]: row["original_value"] for row in rows}

    def clear_session_aliases(self, session_id: str) -> None:
        """Delete temporary PII alias mappings for a session."""
        with self._connect() as conn:
            conn.execute("DELETE FROM pii_alias_map WHERE session_id = ?", (session_id,))

    def ensure_session(self, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Create session row if missing or update its timestamp."""
        now_iso = self._utc_now().isoformat()
        meta_json = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (session_id, now_iso, now_iso, meta_json),
            )

    def append_message(
        self,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        model_used: str | None = None,
    ) -> None:
        """Append a chat turn to local session history."""
        self.ensure_session(session_id)
        now_iso = self._utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    message_id, session_id, role, content, model_used, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, model_used, now_iso),
            )

    def get_recent_messages(self, session_id: str, limit: int = 6) -> list[dict[str, Any]]:
        """Return recent messages in chronological order."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, role, content, model_used, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def clear_cache(self) -> None:
        """Invalidate all cached metric snapshots after batch ingestion."""
        with self._connect() as conn:
            conn.execute("DELETE FROM metric_cache")

    def record_decision(self, decision: dict[str, Any]) -> None:
        """Insert or refresh a pending recommendation in local decisions_log."""
        now_iso = self._utc_now().isoformat()
        rec_json = (
            decision["recommendation_json"]
            if isinstance(decision["recommendation_json"], str)
            else json.dumps(decision["recommendation_json"], default=str)
        )
        ev_json = (
            decision["evidence_json"]
            if isinstance(decision["evidence_json"], str)
            else json.dumps(decision["evidence_json"], default=str)
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO decisions_log (
                    decision_id, created_at, domain, title, prompt_summary,
                    recommendation_json, evidence_json, model_used, privacy_mode,
                    source_type, metric_name, baseline_value, user_action,
                    evaluate_after_days
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    title = excluded.title,
                    recommendation_json = excluded.recommendation_json,
                    evidence_json = excluded.evidence_json,
                    model_used = excluded.model_used,
                    synced_to_snowflake = 0
                """,
                (
                    decision["decision_id"],
                    decision.get("created_at") or now_iso,
                    decision["domain"],
                    decision["title"],
                    decision.get("prompt_summary", ""),
                    rec_json,
                    ev_json,
                    decision["model_used"],
                    decision.get("privacy_mode", "masked_cloud"),
                    decision.get("source_type", "interactive"),
                    decision.get("metric_name", ""),
                    decision.get("baseline_value"),
                    decision.get("user_action", "pending"),
                    int(decision.get("evaluate_after_days", 7)),
                ),
            )

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Fetch a single decision row by identifier."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decisions_log WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_decisions(
        self,
        user_action: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List stored decisions filtered optionally by user_action status."""
        with self._connect() as conn:
            if user_action:
                rows = conn.execute(
                    """
                    SELECT * FROM decisions_log
                    WHERE user_action = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_action, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM decisions_log
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def update_decision_action(
        self,
        decision_id: str,
        user_action: str,
        user_feedback: str | None = None,
        actioned_at: str | None = None,
        evaluate_after_days: int | None = None,
    ) -> dict[str, Any] | None:
        """Record user acceptance, dismissal, or completion for a decision."""
        ts = actioned_at or self._utc_now().isoformat()
        with self._connect() as conn:
            if evaluate_after_days is not None:
                conn.execute(
                    """
                    UPDATE decisions_log
                    SET user_action = ?, user_feedback = COALESCE(?, user_feedback),
                        actioned_at = COALESCE(actioned_at, ?),
                        evaluate_after_days = ?,
                        synced_to_snowflake = 0
                    WHERE decision_id = ?
                    """,
                    (user_action, user_feedback, ts, int(evaluate_after_days), decision_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE decisions_log
                    SET user_action = ?, user_feedback = COALESCE(?, user_feedback),
                        actioned_at = COALESCE(actioned_at, ?),
                        synced_to_snowflake = 0
                    WHERE decision_id = ?
                    """,
                    (user_action, user_feedback, ts, decision_id),
                )
        return self.get_decision(decision_id)

    def record_outcome(self, outcome: dict[str, Any]) -> None:
        """Persist evaluated decision outcome into decision_outcomes."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO decision_outcomes (
                    outcome_id, decision_id, evaluated_at, baseline_value,
                    observed_value, delta_impact, outcome_status, user_feedback
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(outcome_id) DO UPDATE SET
                    evaluated_at = excluded.evaluated_at,
                    observed_value = excluded.observed_value,
                    delta_impact = excluded.delta_impact,
                    outcome_status = excluded.outcome_status,
                    user_feedback = excluded.user_feedback,
                    synced_to_snowflake = 0
                """,
                (
                    outcome["outcome_id"],
                    outcome["decision_id"],
                    outcome["evaluated_at"],
                    outcome.get("baseline_value"),
                    outcome.get("observed_value"),
                    float(outcome["delta_impact"]),
                    outcome["outcome_status"],
                    outcome.get("user_feedback"),
                ),
            )

    def list_outcomes(self, decision_id: str | None = None) -> list[dict[str, Any]]:
        """Return evaluated decision outcomes ordered newest first."""
        with self._connect() as conn:
            if decision_id:
                rows = conn.execute(
                    """
                    SELECT * FROM decision_outcomes
                    WHERE decision_id = ?
                    ORDER BY evaluated_at DESC
                    """,
                    (decision_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decision_outcomes ORDER BY evaluated_at DESC"
                ).fetchall()
            return [dict(row) for row in rows]

    def enqueue_ingest_event(self, entry_id: str, domain: str, payload: dict[str, Any]) -> None:
        """Buffer a single personal data event locally before batch flush."""
        now_iso = self._utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingest_buffer (entry_id, domain, payload_json, created_at, sync_status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (entry_id, domain, json.dumps(payload, default=str), now_iso),
            )

    def list_pending_ingest_events(self, domain: str | None = None) -> list[dict[str, Any]]:
        """List buffered events awaiting batch sync."""
        with self._connect() as conn:
            if domain:
                rows = conn.execute(
                    """
                    SELECT * FROM ingest_buffer
                    WHERE sync_status = 'pending' AND domain = ?
                    ORDER BY created_at ASC
                    """,
                    (domain,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM ingest_buffer
                    WHERE sync_status = 'pending'
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            return [dict(row) for row in rows]

    def mark_ingest_events_synced(self, entry_ids: list[str]) -> int:
        """Mark flushed ingest events as synced."""
        if not entry_ids:
            return 0
        now_iso = self._utc_now().isoformat()
        placeholders = ",".join("?" for _ in entry_ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE ingest_buffer
                SET sync_status = 'synced', synced_at = ?
                WHERE entry_id IN ({placeholders})
                """,
                [now_iso, *entry_ids],
            )
            return int(cur.rowcount)

    def _mark_table_synced_to_snowflake(
        self,
        table: str,
        id_column: str,
        row_ids: list[str],
    ) -> int:
        if not row_ids:
            return 0
        placeholders = ",".join("?" for _ in row_ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE {table} SET synced_to_snowflake = 1 WHERE {id_column} IN ({placeholders})",
                row_ids,
            )
            return int(cur.rowcount)

    def list_unsynced_decisions(self, limit: int = 200) -> list[dict[str, Any]]:
        """List local decisions not yet replicated to Snowflake ANALYTICS.FCT_DECISIONS_LOG."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM decisions_log
                WHERE synced_to_snowflake = 0
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_decisions_synced(self, decision_ids: list[str]) -> int:
        """Mark decisions_log rows as replicated to Snowflake."""
        return self._mark_table_synced_to_snowflake("decisions_log", "decision_id", decision_ids)

    def list_unsynced_outcomes(self, limit: int = 200) -> list[dict[str, Any]]:
        """List local outcomes not yet replicated to ANALYTICS.FCT_DECISION_OUTCOMES."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM decision_outcomes
                WHERE synced_to_snowflake = 0
                ORDER BY evaluated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_outcomes_synced(self, outcome_ids: list[str]) -> int:
        """Mark decision_outcomes rows as replicated to Snowflake."""
        return self._mark_table_synced_to_snowflake("decision_outcomes", "outcome_id", outcome_ids)
