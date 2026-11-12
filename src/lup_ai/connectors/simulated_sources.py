"""Simulated external-format connectors feeding BatchIngestor without live OAuth APIs."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from lup_ai.data.batch_ingestor import (
    BatchIngestor,
    FinanceEventInput,
    FlushResult,
    HabitsSleepEventInput,
    TimeBlockEventInput,
)

DomainName = Literal["finance", "habits_sleep", "time_calendar"]


class SimulatedSyncReport(BaseModel):
    """Summary returned after pulling and normalizing simulated external feeds."""

    source_mode: str = "simulated_external_feeds"
    days_pulled: int
    events_captured: int
    by_domain: dict[str, int] = Field(default_factory=dict)
    flushed: bool
    flush_result: FlushResult | None = None
    sample_external_records: list[dict[str, Any]] = Field(default_factory=list)


class SimulatedBankConnector:
    """Simulate external banking movement payloads and map them to FinanceEventInput."""

    source_name = "simulated_bank_feed"
    domain: DomainName = "finance"

    @staticmethod
    def map_external_record(raw: dict[str, Any]) -> FinanceEventInput:
        """Transform a simulated bank extract record into FinanceEventInput."""
        direction_raw = str(raw.get("tipo", "DEBIT")).upper()
        direction: Literal["income", "expense", "saving"] = (
            "income"
            if direction_raw in {"CREDIT", "INCOME", "ABONO"}
            else ("saving" if direction_raw in {"SAVING", "TRANSFER_SAVINGS"} else "expense")
        )
        category = str(raw.get("categoria_banco", "GENERAL")).upper()
        is_disc = category in {"DELIVERY", "ENTERTAINMENT", "DINING", "SHOPPING", "COFFEE"}
        return FinanceEventInput(
            tx_timestamp=str(raw["posted_at"]),
            amount=float(raw["valor_cop"]),
            currency=str(raw.get("moneda", "COP")),
            direction=direction,
            category_raw=category,
            merchant_raw=str(raw.get("descripcion_comercio", "")),
            account_alias=str(raw.get("producto_mascara", "Cuenta ****4821")),
            is_discretionary=bool(raw.get("discrecional", is_disc)),
            notes_raw=f"Importado desde {raw.get('banco_origen', 'SimBank')}",
            source_system="simulated_bank_feed",
        )

    def pull_raw_records(
        self,
        days: int = 3,
        seed: int = 101,
        anchor_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Generate realistic bank feed JSON records for recent days."""
        rng = np.random.default_rng(seed)
        end_dt = anchor_date or date(2026, 11, 10)
        records: list[dict[str, Any]] = []
        catalog = [
            ("GROCERIES", "Supermercado Regional", "DEBIT", False, (26000, 48000)),
            ("DELIVERY", "App Domicilios Norte", "DEBIT", True, (34000, 68000)),
            ("COFFEE", "Cafe Local Barrio", "DEBIT", True, (12000, 24000)),
            ("ENTERTAINMENT", "Cine y Cultura", "DEBIT", True, (45000, 95000)),
        ]
        for offset in range(days):
            day = end_dt - timedelta(days=offset)
            chosen_idx = int(rng.integers(0, len(catalog)))
            cat, merchant, tipo, disc, (low, high) = catalog[chosen_idx]
            ts = datetime.combine(day, time(int(rng.integers(10, 21)), int(rng.integers(0, 59))))
            records.append(
                {
                    "bank_ref": f"SIMBK-{day.strftime('%Y%m%d')}-{offset:02d}",
                    "banco_origen": "Banco Simulado Central",
                    "producto_mascara": "Tarjeta Credito ****7744" if disc else "Debito ****3310",
                    "posted_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "valor_cop": round(float(rng.uniform(low, high)), 2),
                    "moneda": "COP",
                    "tipo": tipo,
                    "categoria_banco": cat,
                    "descripcion_comercio": merchant,
                    "discrecional": disc,
                }
            )
        return records


class SimulatedWearableConnector:
    """Simulate external sleep & habit tracker payloads into HabitsSleepEventInput."""

    source_name = "simulated_wearable_feed"
    domain: DomainName = "habits_sleep"

    @staticmethod
    def map_external_record(raw: dict[str, Any]) -> HabitsSleepEventInput:
        """Transform a simulated wearable summary into HabitsSleepEventInput."""
        sleep_hours = round(float(raw["duration_min"]) / 60.0, 2)
        return HabitsSleepEventInput(
            log_date=str(raw["sleep_date"]),
            sleep_start_ts=str(raw["bed_time"]),
            sleep_end_ts=str(raw["wake_time"]),
            sleep_hours=sleep_hours,
            sleep_quality_score=float(raw["quality_pct"]),
            exercise_minutes=int(raw["active_min"]),
            habit_completed_count=int(raw["daily_habits_done"]),
            habit_total_count=int(raw.get("daily_habits_target", 5)),
            energy_score=float(raw["readiness_10"]),
            notes_raw=f"Sync dispositivo simulado {raw.get('device_id', 'SIM-WATCH-01')}",
            source_system="simulated_wearable_feed",
        )

    def pull_raw_records(
        self,
        days: int = 3,
        seed: int = 101,
        anchor_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Generate realistic daily wearable telemetry records."""
        rng = np.random.default_rng(seed + 17)
        end_dt = anchor_date or date(2026, 11, 10)
        records: list[dict[str, Any]] = []
        for offset in range(days):
            day = end_dt - timedelta(days=offset)
            sleep_h = float(np.clip(rng.normal(7.35, 0.45), 5.2, 9.0))
            wake_dt = datetime.combine(day, time(6, int(rng.integers(10, 50))))
            bed_dt = wake_dt - timedelta(hours=sleep_h)
            records.append(
                {
                    "device_id": "SIM-WATCH-V2",
                    "sleep_date": day.isoformat(),
                    "bed_time": bed_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "wake_time": wake_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_min": int(round(sleep_h * 60)),
                    "quality_pct": round(float(np.clip(62 + (sleep_h - 6.0) * 12, 45, 96)), 1),
                    "active_min": int(rng.choice([0, 30, 45, 60])),
                    "daily_habits_done": int(rng.integers(3, 6)),
                    "daily_habits_target": 5,
                    "readiness_10": round(float(np.clip(5.2 + (sleep_h - 6.0) * 1.1, 3.0, 9.6)), 1),
                }
            )
        return records


class SimulatedCalendarConnector:
    """Simulate external calendar event feeds into TimeBlockEventInput."""

    source_name = "simulated_calendar_feed"
    domain: DomainName = "time_calendar"

    @staticmethod
    def map_external_record(raw: dict[str, Any]) -> TimeBlockEventInput:
        """Transform a simulated calendar event into TimeBlockEventInput."""
        start_dt = datetime.fromisoformat(str(raw["start_iso"]))
        end_dt = datetime.fromisoformat(str(raw["end_iso"]))
        minutes = max(15, int((end_dt - start_dt).total_seconds() // 60))
        btype_raw = str(raw.get("category_hint", "DEEP_WORK")).upper()
        valid_types = {"DEEP_WORK", "MEETING", "LEISURE", "ADMIN", "REST"}
        block_type: Literal["DEEP_WORK", "MEETING", "LEISURE", "ADMIN", "REST"] = (
            btype_raw if btype_raw in valid_types else "ADMIN"  # type: ignore[assignment]
        )
        return TimeBlockEventInput(
            start_ts=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_ts=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration_minutes=minutes,
            domain_tag=str(raw.get("calendar_name", "work")).lower(),
            block_type=block_type,
            title_raw=str(raw.get("summary", "Bloque de calendario")),
            priority="high" if block_type == "DEEP_WORK" else "medium",
            completed_flag=bool(raw.get("done", True)),
            source_system="simulated_calendar_feed",
        )

    def pull_raw_records(
        self,
        days: int = 3,
        seed: int = 101,
        anchor_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Generate realistic calendar event records."""
        rng = np.random.default_rng(seed + 31)
        end_dt = anchor_date or date(2026, 11, 10)
        records: list[dict[str, Any]] = []
        for offset in range(days):
            day = end_dt - timedelta(days=offset)
            dw_start = datetime.combine(day, time(8, 0))
            dw_end = dw_start + timedelta(minutes=int(rng.choice([90, 120, 150])))
            records.append(
                {
                    "uid": f"cal-dw-{day.isoformat()}",
                    "start_iso": dw_start.isoformat(),
                    "end_iso": dw_end.isoformat(),
                    "summary": "Bloque tecnico sin interrupciones",
                    "calendar_name": "work",
                    "category_hint": "DEEP_WORK",
                    "done": True,
                }
            )
            mt_start = datetime.combine(day, time(14, 0))
            mt_end = mt_start + timedelta(minutes=int(rng.choice([45, 60, 90])))
            records.append(
                {
                    "uid": f"cal-mt-{day.isoformat()}",
                    "start_iso": mt_start.isoformat(),
                    "end_iso": mt_end.isoformat(),
                    "summary": "Seguimiento operativo equipo",
                    "calendar_name": "work",
                    "category_hint": "MEETING",
                    "done": True,
                }
            )
        return records


class SimulatedConnectorHub:
    """Coordinate simulated external sources and ingest them through BatchIngestor."""

    def __init__(self, ingestor: BatchIngestor) -> None:
        self._ingestor = ingestor
        self.bank = SimulatedBankConnector()
        self.wearable = SimulatedWearableConnector()
        self.calendar = SimulatedCalendarConnector()

    def sync_all(
        self,
        days: int = 3,
        seed: int = 101,
        auto_flush: bool = True,
        sources: list[DomainName] | None = None,
        anchor_date: date | None = None,
    ) -> SimulatedSyncReport:
        """Pull simulated external feeds, buffer in SQLite, and optionally batch-flush."""
        enabled = set(sources or ["finance", "habits_sleep", "time_calendar"])
        counts: dict[str, int] = {}
        samples: list[dict[str, Any]] = []

        if "finance" in enabled:
            raw_fin = self.bank.pull_raw_records(days=days, seed=seed, anchor_date=anchor_date)
            for item in raw_fin:
                self._ingestor.capture_finance(self.bank.map_external_record(item))
            counts["finance"] = len(raw_fin)
            if raw_fin:
                samples.append({"domain": "finance", "raw_sample": raw_fin[0]})

        if "habits_sleep" in enabled:
            raw_hab = self.wearable.pull_raw_records(days=days, seed=seed, anchor_date=anchor_date)
            for item in raw_hab:
                self._ingestor.capture_habits_sleep(self.wearable.map_external_record(item))
            counts["habits_sleep"] = len(raw_hab)
            if raw_hab:
                samples.append({"domain": "habits_sleep", "raw_sample": raw_hab[0]})

        if "time_calendar" in enabled:
            raw_cal = self.calendar.pull_raw_records(days=days, seed=seed, anchor_date=anchor_date)
            for item in raw_cal:
                self._ingestor.capture_time_block(self.calendar.map_external_record(item))
            counts["time_calendar"] = len(raw_cal)
            if raw_cal:
                samples.append({"domain": "time_calendar", "raw_sample": raw_cal[0]})

        flush_res: FlushResult | None = None
        if auto_flush:
            flush_res = self._ingestor.flush_batch()

        return SimulatedSyncReport(
            days_pulled=days,
            events_captured=sum(counts.values()),
            by_domain=counts,
            flushed=auto_flush,
            flush_result=flush_res,
            sample_external_records=samples,
        )
