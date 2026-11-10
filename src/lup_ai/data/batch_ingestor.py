"""Local SQLite batch buffer and bulk flush to Snowflake RAW or local CSV mirror."""

from __future__ import annotations

import csv
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore


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


def _now_local(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


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
    ) -> None:
        self._settings = settings
        self._store = store
        self._csv_repo = local_csv_repo or LocalCsvAnalyticsRepository()

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
        has_auth = bool(
            self._settings.snowflake_password or self._settings.snowflake_private_key_path
        )
        return bool(self._settings.snowflake_account and self._settings.snowflake_user and has_auth)

    def _flush_to_snowflake(self, grouped: dict[str, list[dict[str, Any]]]) -> None:
        import snowflake.connector

        conn_kwargs: dict[str, Any] = {
            "account": self._settings.snowflake_account,
            "user": self._settings.snowflake_user,
            "role": self._settings.snowflake_role_ingest,
            "warehouse": self._settings.snowflake_warehouse,
            "database": self._settings.snowflake_database,
            "schema": "RAW",
            "password": self._settings.snowflake_password,
        }
        conn = snowflake.connector.connect(**conn_kwargs)
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
        """Flush all pending buffered events in a single batch to Snowflake or local CSV mirror."""
        pending = self._store.list_pending_ingest_events()
        if not pending:
            target = "snowflake" if self._snowflake_ingest_configured() else "local_synthetic_csv"
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

        if self._snowflake_ingest_configured():
            self._flush_to_snowflake(grouped)
            target = "snowflake"
        else:
            self._flush_to_local_csv(grouped)
            target = "local_synthetic_csv"

        self._store.mark_ingest_events_synced(entry_ids)
        self._store.clear_cache()

        return FlushResult(
            target_backend=target,
            flushed_count=len(entry_ids),
            by_domain={k: len(v) for k, v in grouped.items() if v},
        )
