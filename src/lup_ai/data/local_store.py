"""Local SQLite store for OLTP state, TTL metric cache, goals mirror, and PII alias map."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class LocalStore:
    """Manage local SQLite persistence to avoid OLTP load and unnecessary Snowflake wake-ups."""

    def __init__(
        self,
        db_path: Path | str,
        settings: Any | None = None,
        snowflake_conn_factory: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings = settings
        self._snowflake_conn_factory = snowflake_conn_factory
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

    def _open_snowflake_connection(self) -> Any | None:
        if self._snowflake_conn_factory is not None:
            return self._snowflake_conn_factory(self._settings)
        cfg = self._settings
        if cfg is None:
            try:
                from lup_ai.config.settings import get_settings

                cfg = get_settings()
            except Exception:
                return None
        try:
            from lup_ai.data.snowflake_client import (
                build_snowflake_connect_kwargs,
                has_valid_snowflake_credentials,
            )

            if not has_valid_snowflake_credentials(cfg):
                return None
            import snowflake.connector

            conn_kwargs = build_snowflake_connect_kwargs(
                cfg,
                role=cfg.snowflake_role_ingest,
                warehouse=cfg.snowflake_warehouse,
                database=cfg.snowflake_database,
                schema="ANALYTICS",
            )
            return snowflake.connector.connect(**conn_kwargs)
        except Exception:
            return None

    def _sync_goal_to_snowflake(self, goal: dict[str, Any], action: str = "upsert") -> None:
        conn = self._open_snowflake_connection()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                if action == "delete":
                    cur.execute(
                        "DELETE FROM ANALYTICS.DIM_GOALS WHERE goal_id = %s",
                        (str(goal["goal_id"]),),
                    )
                else:
                    cur.execute(
                        """
                        MERGE INTO ANALYTICS.DIM_GOALS AS tgt
                        USING (
                            SELECT
                                %s AS goal_id,
                                %s AS domain,
                                %s AS title,
                                %s AS metric_name,
                                %s AS target_value,
                                %s AS unit,
                                %s AS comparison_operator,
                                TO_DATE(%s) AS deadline,
                                %s AS priority,
                                %s AS status
                        ) AS src
                        ON tgt.goal_id = src.goal_id
                        WHEN MATCHED THEN UPDATE SET
                            tgt.domain = src.domain,
                            tgt.title = src.title,
                            tgt.metric_name = src.metric_name,
                            tgt.target_value = src.target_value,
                            tgt.unit = src.unit,
                            tgt.comparison_operator = src.comparison_operator,
                            tgt.deadline = src.deadline,
                            tgt.priority = src.priority,
                            tgt.status = src.status,
                            tgt.updated_at = CURRENT_TIMESTAMP()
                        WHEN NOT MATCHED THEN INSERT (
                            goal_id, domain, title, metric_name, target_value,
                            unit, comparison_operator, deadline, priority, status
                        ) VALUES (
                            src.goal_id, src.domain, src.title, src.metric_name,
                            src.target_value, src.unit, src.comparison_operator,
                            src.deadline, src.priority, src.status
                        )
                        """,
                        (
                            str(goal["goal_id"]),
                            str(goal["domain"]),
                            str(goal["title"]),
                            str(goal["metric_name"]),
                            float(goal["target_value"]),
                            str(goal["unit"]),
                            str(goal.get("comparison_operator") or ">="),
                            str(goal["deadline"])[:10] if goal.get("deadline") else None,
                            int(goal.get("priority", 1)),
                            str(goal.get("status", "active")),
                        ),
                    )
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _sync_preference_to_snowflake(self, pref: dict[str, Any], action: str = "upsert") -> None:
        conn = self._open_snowflake_connection()
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                if action == "delete":
                    cur.execute(
                        "DELETE FROM ANALYTICS.DIM_PREFERENCES WHERE pref_id = %s",
                        (str(pref["pref_id"]),),
                    )
                else:
                    cur.execute(
                        """
                        MERGE INTO ANALYTICS.DIM_PREFERENCES AS tgt
                        USING (
                            SELECT
                                %s AS pref_id,
                                %s AS domain,
                                %s AS key_name,
                                %s AS value_json,
                                %s AS is_hard_constraint
                        ) AS src
                        ON tgt.pref_id = src.pref_id
                        WHEN MATCHED THEN UPDATE SET
                            tgt.domain = src.domain,
                            tgt.key_name = src.key_name,
                            tgt.value_json = src.value_json,
                            tgt.is_hard_constraint = src.is_hard_constraint,
                            tgt.updated_at = CURRENT_TIMESTAMP()
                        WHEN NOT MATCHED THEN INSERT (
                            pref_id, domain, key_name, value_json, is_hard_constraint
                        ) VALUES (
                            src.pref_id, src.domain, src.key_name,
                            src.value_json, src.is_hard_constraint
                        )
                        """,
                        (
                            str(pref["pref_id"]),
                            str(pref["domain"]),
                            str(pref["key_name"]),
                            str(pref["value_json"]),
                            bool(pref.get("is_hard_constraint", False)),
                        ),
                    )
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def create_goal(self, goal_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new goal in the SQLite mirror and sync to Snowflake."""
        now_iso = self._utc_now().isoformat()
        domain = str(goal_data["domain"])
        goal_id = goal_data.get("goal_id")
        if not goal_id:
            domain_slug = domain.lower().replace(" ", "_")
            goal_id = f"goal_{domain_slug}_{uuid.uuid4().hex[:8]}"

        record = {
            "goal_id": str(goal_id),
            "domain": domain,
            "title": str(goal_data["title"]),
            "metric_name": str(goal_data["metric_name"]),
            "target_value": float(goal_data["target_value"]),
            "unit": str(goal_data["unit"]),
            "comparison_operator": str(goal_data.get("comparison_operator") or ">="),
            "deadline": str(goal_data["deadline"]) if goal_data.get("deadline") else None,
            "priority": int(goal_data.get("priority", 1)),
            "status": str(goal_data.get("status", "active")),
            "updated_at": now_iso,
        }
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
                    record["goal_id"],
                    record["domain"],
                    record["title"],
                    record["metric_name"],
                    record["target_value"],
                    record["unit"],
                    record["comparison_operator"],
                    record["deadline"],
                    record["priority"],
                    record["status"],
                    record["updated_at"],
                ),
            )
        self._sync_goal_to_snowflake(record, action="upsert")
        self.clear_cache()
        return self.get_goal(record["goal_id"]) or record

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        """Fetch a single goal from the local mirror."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT goal_id, domain, title, metric_name, target_value,
                       unit, comparison_operator, deadline, priority, status, updated_at
                FROM goals_mirror
                WHERE goal_id = ?
                """,
                (goal_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def update_goal(self, goal_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing goal in the SQLite mirror and sync to Snowflake."""
        current = self.get_goal(goal_id)
        if current is None:
            return None

        allowed_fields = {
            "domain",
            "title",
            "metric_name",
            "target_value",
            "unit",
            "comparison_operator",
            "deadline",
            "priority",
            "status",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}
        if not filtered:
            return current

        now_iso = self._utc_now().isoformat()
        merged = {**current, **filtered, "updated_at": now_iso}
        if "target_value" in merged:
            merged["target_value"] = float(merged["target_value"])
        if "priority" in merged:
            merged["priority"] = int(merged["priority"])

        set_clauses = [f"{k} = ?" for k in filtered.keys()]
        set_clauses.append("updated_at = ?")
        values = [merged[k] for k in filtered.keys()]
        values.append(now_iso)
        values.append(goal_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE goals_mirror SET {', '.join(set_clauses)} WHERE goal_id = ?",
                values,
            )

        self._sync_goal_to_snowflake(merged, action="upsert")
        self.clear_cache()
        return self.get_goal(goal_id)

    def delete_goal(self, goal_id: str) -> bool:
        """Delete a goal from the SQLite mirror and sync to Snowflake."""
        current = self.get_goal(goal_id)
        if current is None:
            return False

        with self._connect() as conn:
            conn.execute("DELETE FROM goals_mirror WHERE goal_id = ?", (goal_id,))

        self._sync_goal_to_snowflake(current, action="delete")
        self.clear_cache()
        return True

    def create_preference(self, pref_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new preference in the SQLite mirror and sync to Snowflake."""
        now_iso = self._utc_now().isoformat()
        domain = str(pref_data["domain"])
        key_name = str(pref_data["key_name"])
        pref_id = pref_data.get("pref_id")
        if not pref_id:
            domain_slug = domain.lower().replace(" ", "_")
            key_slug = key_name.lower().replace(" ", "_")
            pref_id = f"pref_{domain_slug}_{key_slug}"

        value_json = pref_data.get("value_json", "{}")
        if isinstance(value_json, (dict, list)):
            value_json = json.dumps(value_json)
        else:
            value_json = str(value_json)

        is_hard = bool(pref_data.get("is_hard_constraint", False))

        record = {
            "pref_id": str(pref_id),
            "domain": domain,
            "key_name": key_name,
            "value_json": value_json,
            "is_hard_constraint": is_hard,
            "updated_at": now_iso,
        }

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
                    record["pref_id"],
                    record["domain"],
                    record["key_name"],
                    record["value_json"],
                    1 if is_hard else 0,
                    record["updated_at"],
                ),
            )

        self._sync_preference_to_snowflake(record, action="upsert")
        self.clear_cache()
        return self.get_preference(record["pref_id"]) or record

    def get_preference(self, pref_id: str) -> dict[str, Any] | None:
        """Fetch a single preference from the local mirror."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT pref_id, domain, key_name, value_json, is_hard_constraint, updated_at
                FROM preferences_mirror
                WHERE pref_id = ?
                """,
                (pref_id,),
            ).fetchone()
            if row is None:
                return None
            res = dict(row)
            res["is_hard_constraint"] = bool(res["is_hard_constraint"])
            return res

    def update_preference(self, pref_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing preference in the SQLite mirror and sync to Snowflake."""
        current = self.get_preference(pref_id)
        if current is None:
            return None

        allowed_fields = {"domain", "key_name", "value_json", "is_hard_constraint"}
        filtered = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}
        if not filtered:
            return current

        now_iso = self._utc_now().isoformat()
        merged = {**current, **filtered, "updated_at": now_iso}
        if "value_json" in filtered:
            val = filtered["value_json"]
            merged["value_json"] = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
        if "is_hard_constraint" in filtered:
            merged["is_hard_constraint"] = bool(filtered["is_hard_constraint"])

        set_clauses = [f"{k} = ?" for k in filtered.keys()]
        set_clauses.append("updated_at = ?")
        values = []
        for k in filtered.keys():
            if k == "is_hard_constraint":
                values.append(1 if merged[k] else 0)
            elif k == "value_json":
                values.append(merged["value_json"])
            else:
                values.append(merged[k])
        values.append(now_iso)
        values.append(pref_id)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE preferences_mirror SET {', '.join(set_clauses)} WHERE pref_id = ?",
                values,
            )

        self._sync_preference_to_snowflake(merged, action="upsert")
        self.clear_cache()
        return self.get_preference(pref_id)

    def delete_preference(self, pref_id: str) -> bool:
        """Delete a preference from the SQLite mirror and sync to Snowflake."""
        current = self.get_preference(pref_id)
        if current is None:
            return False

        with self._connect() as conn:
            conn.execute("DELETE FROM preferences_mirror WHERE pref_id = ?", (pref_id,))

        self._sync_preference_to_snowflake(current, action="delete")
        self.clear_cache()
        return True

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

    def get_unsynced_decisions(self) -> list[dict[str, Any]]:
        """Fetch local decisions where synced_to_snowflake = 0."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM decisions_log
                WHERE synced_to_snowflake = 0
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_unsynced_decisions(self, limit: int = 200) -> list[dict[str, Any]]:
        """List local decisions not yet replicated to Snowflake ANALYTICS.FCT_DECISIONS_LOG."""
        return self.get_unsynced_decisions()[:limit]

    def mark_decisions_as_synced(self, ids: list[str]) -> int:
        """Mark decisions_log rows as replicated to Snowflake (synced_to_snowflake = 1)."""
        return self._mark_table_synced_to_snowflake("decisions_log", "decision_id", ids)

    def mark_decisions_synced(self, decision_ids: list[str]) -> int:
        """Alias for backward compatibility."""
        return self.mark_decisions_as_synced(decision_ids)

    def get_unsynced_outcomes(self) -> list[dict[str, Any]]:
        """Fetch local decision outcomes where synced_to_snowflake = 0."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM decision_outcomes
                WHERE synced_to_snowflake = 0
                ORDER BY evaluated_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_unsynced_outcomes(self, limit: int = 200) -> list[dict[str, Any]]:
        """List local outcomes not yet replicated to ANALYTICS.FCT_DECISION_OUTCOMES."""
        return self.get_unsynced_outcomes()[:limit]

    def mark_outcomes_as_synced(self, ids: list[str]) -> int:
        """Mark decision_outcomes rows as replicated to Snowflake (synced_to_snowflake = 1)."""
        return self._mark_table_synced_to_snowflake("decision_outcomes", "outcome_id", ids)

    def mark_outcomes_synced(self, outcome_ids: list[str]) -> int:
        """Alias for backward compatibility."""
        return self.mark_outcomes_as_synced(outcome_ids)
