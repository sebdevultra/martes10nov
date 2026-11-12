"""Local SQLite batch buffer and bulk flush to Snowflake RAW or local CSV mirror."""

from __future__ import annotations

import csv
import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.snowflake_client import (
    build_snowflake_connect_kwargs,
    has_valid_snowflake_credentials,
)


class FinanceEventInput(BaseModel):
    """Input schema for capturing a personal finance event."""

    tx_timestamp: str | None = None
    amount: float = Field(..., gt=0)
    currency: str = "COP"
    direction: Literal["income", "expense", "saving"] = "expense"
    category_raw: str = Field(..., min_length=1, max_length=64)
    merchant_raw: str = ""
    account_alias: str = ""
    is_discretionary: bool = True
    notes_raw: str = ""
    source_system: str = "manual_capture"


class HabitsSleepEventInput(BaseModel):
    """Input schema for capturing a daily habits and sleep log."""

    log_date: str | None = None
    sleep_start_ts: str | None = None
    sleep_end_ts: str | None = None
    sleep_hours: float = Field(..., ge=0.0, le=24.0)
    sleep_quality_score: float = Field(default=75.0, ge=0.0, le=100.0)
    exercise_minutes: int = Field(default=0, ge=0, le=600)
    habit_completed_count: int = Field(default=0, ge=0, le=50)
    habit_total_count: int = Field(default=5, ge=0, le=50)
    energy_score: float = Field(default=7.0, ge=1.0, le=10.0)
    notes_raw: str = ""
    source_system: str = "manual_capture"


class TimeBlockEventInput(BaseModel):
    """Input schema for capturing a calendar/time allocation block."""

    start_ts: str | None = None
    end_ts: str | None = None
    duration_minutes: int = Field(..., gt=0, le=1440)
    domain_tag: str = Field(default="work", min_length=1, max_length=32)
    block_type: Literal["DEEP_WORK", "MEETING", "LEISURE", "ADMIN", "REST"] = "DEEP_WORK"
    title_raw: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    completed_flag: bool = True
    source_system: str = "manual_capture"


class CaptureResult(BaseModel):
    """Confirmation of buffered local event."""

    entry_id: str
    domain: str
    sync_status: str = "pending"


class FlushResult(BaseModel):
    """Summary of batch synchronization from SQLite buffer to storage target."""

    target_backend: str
    flushed_count: int
    by_domain: dict[str, int] = Field(default_factory=dict)
    decisions_synced: int = 0
    outcomes_synced: int = 0


def _now_local(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def _to_snowflake_ntz(raw_ts: str | None) -> str:
    """Normalize an ISO timestamp string into UTC YYYY-MM-DD HH:MM:SS.ffffff."""
    if not raw_ts:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    try:
        parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return raw_ts[:26]


def _append_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    file_exists = csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


class BatchIngestor:
    """Buffer daily events locally in SQLite and flush in batches to avoid OLTP Snowflake calls."""

    def __init__(
        self,
        settings: Settings,
        store: LocalStore,
        local_csv_repo: LocalCsvAnalyticsRepository | None = None,
        snowflake_conn_factory: Callable[[Settings], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._csv_repo = local_csv_repo or LocalCsvAnalyticsRepository()
        self._snowflake_conn_factory = snowflake_conn_factory

    def capture_finance(self, event: FinanceEventInput) -> CaptureResult:
        """Validate and buffer a finance transaction in local SQLite."""
        now_dt = _now_local(self._settings.lup_timezone)
        entry_id = f"tx_cap_{uuid.uuid4().hex[:12]}"
        payload = {
            "tx_id": entry_id,
            "tx_timestamp": event.tx_timestamp or now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": round(event.amount, 2),
            "currency": event.currency.upper(),
            "direction": event.direction,
            "category_raw": event.category_raw.upper(),
            "merchant_raw": event.merchant_raw,
            "account_alias": event.account_alias,
            "is_discretionary": event.is_discretionary,
            "notes_raw": event.notes_raw,
            "source_system": event.source_system,
        }
        self._store.enqueue_ingest_event(entry_id=entry_id, domain="finance", payload=payload)
        return CaptureResult(entry_id=entry_id, domain="finance")

    def capture_habits_sleep(self, event: HabitsSleepEventInput) -> CaptureResult:
        """Validate and buffer a habits/sleep log in local SQLite."""
        now_dt = _now_local(self._settings.lup_timezone)
        log_date = event.log_date or date.today().isoformat()
        entry_id = f"hab_cap_{uuid.uuid4().hex[:12]}"
        payload = {
            "record_id": entry_id,
            "log_date": log_date,
            "sleep_start_ts": event.sleep_start_ts or f"{log_date} 23:00:00",
            "sleep_end_ts": event.sleep_end_ts or f"{log_date} 06:30:00",
            "sleep_hours": round(event.sleep_hours, 2),
            "sleep_quality_score": round(event.sleep_quality_score, 2),
            "exercise_minutes": event.exercise_minutes,
            "habit_completed_count": event.habit_completed_count,
            "habit_total_count": event.habit_total_count,
            "energy_score": round(event.energy_score, 2),
            "notes_raw": event.notes_raw,
            "source_system": event.source_system or now_dt.strftime("%Y-%m-%d"),
        }
        self._store.enqueue_ingest_event(entry_id=entry_id, domain="habits_sleep", payload=payload)
        return CaptureResult(entry_id=entry_id, domain="habits_sleep")

    def capture_time_block(self, event: TimeBlockEventInput) -> CaptureResult:
        """Validate and buffer a calendar/time block in local SQLite."""
        now_dt = _now_local(self._settings.lup_timezone)
        start_str = event.start_ts or now_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = event.end_ts or now_dt.strftime("%Y-%m-%d %H:%M:%S")
        entry_id = f"evt_cap_{uuid.uuid4().hex[:12]}"
        payload = {
            "event_id": entry_id,
            "start_ts": start_str,
            "end_ts": end_str,
            "duration_minutes": event.duration_minutes,
            "domain_tag": event.domain_tag.lower(),
            "block_type": event.block_type,
            "title_raw": event.title_raw,
            "priority": event.priority,
            "completed_flag": event.completed_flag,
            "source_system": event.source_system,
        }
        self._store.enqueue_ingest_event(entry_id=entry_id, domain="time_calendar", payload=payload)
        return CaptureResult(entry_id=entry_id, domain="time_calendar")

    def _snowflake_ingest_configured(self) -> bool:
        return has_valid_snowflake_credentials(self._settings)

    def _open_snowflake_connection(self) -> Any:
        if self._snowflake_conn_factory is not None:
            return self._snowflake_conn_factory(self._settings)

        import snowflake.connector

        conn_kwargs = build_snowflake_connect_kwargs(
            self._settings,
            role=self._settings.snowflake_role_ingest,
            warehouse=self._settings.snowflake_warehouse,
            database=self._settings.snowflake_database,
            schema="RAW",
        )
        return snowflake.connector.connect(**conn_kwargs)

    def _flush_to_snowflake(
        self,
        grouped: dict[str, list[dict[str, Any]]],
        decisions: list[dict[str, Any]],
        outcomes: list[dict[str, Any]],
    ) -> None:
        conn = self._open_snowflake_connection()
        try:
            with conn.cursor() as cur:
                if grouped["finance"]:
                    cur.executemany(
                        """
                        INSERT INTO RAW.RAW_FINANCE_TX (
                            tx_id, tx_timestamp, amount, currency, direction, category_raw,
                            merchant_raw, account_alias, is_discretionary, notes_raw, source_system
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                r["tx_id"],
                                r["tx_timestamp"],
                                r["amount"],
                                r["currency"],
                                r["direction"],
                                r["category_raw"],
                                r["merchant_raw"],
                                r["account_alias"],
                                r["is_discretionary"],
                                r["notes_raw"],
                                r["source_system"],
                            )
                            for r in grouped["finance"]
                        ],
                    )
                if grouped["habits_sleep"]:
                    cur.executemany(
                        """
                        INSERT INTO RAW.RAW_HABITS_SLEEP (
                            record_id, log_date, sleep_start_ts, sleep_end_ts, sleep_hours,
                            sleep_quality_score, exercise_minutes, habit_completed_count,
                            habit_total_count, energy_score, notes_raw, source_system
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                r["record_id"],
                                r["log_date"],
                                r["sleep_start_ts"],
                                r["sleep_end_ts"],
                                r["sleep_hours"],
                                r["sleep_quality_score"],
                                r["exercise_minutes"],
                                r["habit_completed_count"],
                                r["habit_total_count"],
                                r["energy_score"],
                                r["notes_raw"],
                                r["source_system"],
                            )
                            for r in grouped["habits_sleep"]
                        ],
                    )
                if grouped["time_calendar"]:
                    cur.executemany(
                        """
                        INSERT INTO RAW.RAW_TIME_CALENDAR (
                            event_id, start_ts, end_ts, duration_minutes, domain_tag,
                            block_type, title_raw, priority, completed_flag, source_system
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                r["event_id"],
                                r["start_ts"],
                                r["end_ts"],
                                r["duration_minutes"],
                                r["domain_tag"],
                                r["block_type"],
                                r["title_raw"],
                                r["priority"],
                                r["completed_flag"],
                                r["source_system"],
                            )
                            for r in grouped["time_calendar"]
                        ],
                    )
                if decisions:
                    cur.executemany(
                        """
                        MERGE INTO ANALYTICS.FCT_DECISIONS_LOG AS tgt
                        USING (
                            SELECT
                                %s AS decision_id,
                                TO_TIMESTAMP_NTZ(%s) AS created_at,
                                %s AS domain,
                                %s AS prompt_summary,
                                PARSE_JSON(%s) AS recommendation_json,
                                PARSE_JSON(%s) AS evidence_json,
                                %s AS model_used,
                                %s AS privacy_mode,
                                %s AS user_action
                        ) AS src
                        ON tgt.decision_id = src.decision_id
                        WHEN MATCHED THEN UPDATE SET
                            tgt.user_action = src.user_action,
                            tgt.recommendation_json = src.recommendation_json,
                            tgt.evidence_json = src.evidence_json,
                            tgt.model_used = src.model_used
                        WHEN NOT MATCHED THEN INSERT (
                            decision_id, created_at, domain, prompt_summary,
                            recommendation_json, evidence_json, model_used,
                            privacy_mode, user_action
                        ) VALUES (
                            src.decision_id, src.created_at, src.domain, src.prompt_summary,
                            src.recommendation_json, src.evidence_json, src.model_used,
                            src.privacy_mode, src.user_action
                        )
                        """,
                        [
                            (
                                str(d["decision_id"])[:64],
                                _to_snowflake_ntz(d.get("created_at")),
                                str(d["domain"])[:32],
                                str(d.get("prompt_summary") or "")[:512],
                                d["recommendation_json"],
                                d["evidence_json"],
                                str(d["model_used"])[:64],
                                str(d.get("privacy_mode") or "masked_cloud")[:32],
                                str(d.get("user_action") or "pending")[:24],
                            )
                            for d in decisions
                        ],
                    )
                if outcomes:
                    cur.executemany(
                        """
                        MERGE INTO ANALYTICS.FCT_DECISION_OUTCOMES AS tgt
                        USING (
                            SELECT
                                %s AS outcome_id,
                                %s AS decision_id,
                                TO_DATE(%s) AS evaluated_at,
                                %s AS delta_impact,
                                %s AS outcome_status,
                                %s AS user_feedback
                        ) AS src
                        ON tgt.outcome_id = src.outcome_id
                        WHEN MATCHED THEN UPDATE SET
                            tgt.evaluated_at = src.evaluated_at,
                            tgt.delta_impact = src.delta_impact,
                            tgt.outcome_status = src.outcome_status,
                            tgt.user_feedback = src.user_feedback
                        WHEN NOT MATCHED THEN INSERT (
                            outcome_id, decision_id, evaluated_at,
                            delta_impact, outcome_status, user_feedback
                        ) VALUES (
                            src.outcome_id, src.decision_id, src.evaluated_at,
                            src.delta_impact, src.outcome_status, src.user_feedback
                        )
                        """,
                        [
                            (
                                str(o["outcome_id"])[:64],
                                str(o["decision_id"])[:64],
                                str(o["evaluated_at"])[:10],
                                float(o["delta_impact"]),
                                str(o["outcome_status"])[:24],
                                (str(o["user_feedback"])[:512] if o.get("user_feedback") else None),
                            )
                            for o in outcomes
                        ],
                    )
        finally:
            conn.close()

    def _flush_to_local_csv(self, grouped: dict[str, list[dict[str, Any]]]) -> None:
        out = self._csv_repo.data_dir
        _append_csv_rows(
            out / "finance_tx.csv",
            [
                "tx_id",
                "tx_timestamp",
                "amount",
                "currency",
                "direction",
                "category_raw",
                "merchant_raw",
                "account_alias",
                "is_discretionary",
                "notes_raw",
                "source_system",
            ],
            grouped["finance"],
        )
        _append_csv_rows(
            out / "habits_sleep.csv",
            [
                "record_id",
                "log_date",
                "sleep_start_ts",
                "sleep_end_ts",
                "sleep_hours",
                "sleep_quality_score",
                "exercise_minutes",
                "habit_completed_count",
                "habit_total_count",
                "energy_score",
                "notes_raw",
                "source_system",
            ],
            grouped["habits_sleep"],
        )
        _append_csv_rows(
            out / "time_calendar.csv",
            [
                "event_id",
                "start_ts",
                "end_ts",
                "duration_minutes",
                "domain_tag",
                "block_type",
                "title_raw",
                "priority",
                "completed_flag",
                "source_system",
            ],
            grouped["time_calendar"],
        )
        self._csv_repo.invalidate_cache()

    def flush_batch(self) -> FlushResult:
        """Flush buffered events and unsynced decisions/outcomes to Snowflake or CSV mirror."""
        pending = self._store.list_pending_ingest_events()
        sf_enabled = self._snowflake_ingest_configured()
        pending_decisions = self._store.list_unsynced_decisions() if sf_enabled else []
        pending_outcomes = self._store.list_unsynced_outcomes() if sf_enabled else []
        target = "snowflake" if sf_enabled else "local_synthetic_csv"

        if not pending and not pending_decisions and not pending_outcomes:
            return FlushResult(target_backend=target, flushed_count=0, by_domain={})

        grouped: dict[str, list[dict[str, Any]]] = {
            "finance": [],
            "habits_sleep": [],
            "time_calendar": [],
        }
        entry_ids: list[str] = []
        for row in pending:
            domain = row["domain"]
            if domain in grouped:
                grouped[domain].append(json.loads(row["payload_json"]))
                entry_ids.append(row["entry_id"])

        decisions_synced = 0
        outcomes_synced = 0
        if sf_enabled:
            self._flush_to_snowflake(grouped, pending_decisions, pending_outcomes)
            if pending_decisions:
                decisions_synced = self._store.mark_decisions_synced(
                    [str(d["decision_id"]) for d in pending_decisions]
                )
            if pending_outcomes:
                outcomes_synced = self._store.mark_outcomes_synced(
                    [str(o["outcome_id"]) for o in pending_outcomes]
                )
        else:
            self._flush_to_local_csv(grouped)

        if entry_ids:
            self._store.mark_ingest_events_synced(entry_ids)
        if entry_ids or decisions_synced or outcomes_synced:
            self._store.clear_cache()

        return FlushResult(
            target_backend=target,
            flushed_count=len(entry_ids),
            by_domain={k: len(v) for k, v in grouped.items() if v},
            decisions_synced=decisions_synced,
            outcomes_synced=outcomes_synced,
        )
