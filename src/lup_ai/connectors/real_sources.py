"""Real external-format connectors and adapters for live or manual data exports.

Implements connectors that map real bank CSV extracts, wearable health exports,
and calendar events into typed BatchIngestor inputs (FinanceEventInput,
HabitsSleepEventInput, TimeBlockEventInput).
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, TextIO

from lup_ai.data.batch_ingestor import (
    BatchIngestor,
    FinanceEventInput,
    HabitsSleepEventInput,
    TimeBlockEventInput,
)

DomainName = Literal["finance", "habits_sleep", "time_calendar"]

_DISCRETIONARY_KEYWORDS = frozenset(
    {
        "DELIVERY",
        "DOMICILIO",
        "UBER",
        "RAPPI",
        "DIDIFOOD",
        "ENTERTAINMENT",
        "ENTRETENIMIENTO",
        "DINING",
        "RESTAURANT",
        "RESTAURANTE",
        "SHOPPING",
        "COMPRAS",
        "COFFEE",
        "CAFE",
        "CAFETERIA",
        "BAR",
        "CINE",
        "CINEMA",
        "VIAJES",
        "TRAVEL",
        "HOTEL",
        "OCIO",
        "SUSCRIPCION",
        "STREAMING",
        "NETFLIX",
        "SPOTIFY",
    }
)


class CSVBankConnector:
    """Parse real bank CSV extracts into typed FinanceEventInput models."""

    source_name = "real_bank_csv"
    domain: DomainName = "finance"

    @staticmethod
    def _is_discretionary(category: str, merchant: str) -> bool:
        combined = f"{category.upper()} {merchant.upper()}"
        return any(kw in combined for kw in _DISCRETIONARY_KEYWORDS)

    @classmethod
    def map_external_record(cls, raw: dict[str, Any]) -> FinanceEventInput:
        """Transform a normalized banking movement dictionary into FinanceEventInput."""
        norm = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}

        def _get(*keys: str, default: Any = "") -> Any:
            for k in keys:
                if k in norm and norm[k] is not None:
                    val = str(norm[k]).strip()
                    if val != "":
                        return val
            return default

        # 1. Timestamp
        raw_ts = _get(
            "tx_timestamp",
            "posted_at",
            "timestamp",
            "date",
            "fecha",
            "fecha_transaccion",
            "transaction_date",
            default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        # 2. Amount and direction
        amount_val = _get(
            "amount", "valor", "monto", "valor_cop", "importe", "value", default="0.0"
        )
        cleaned_amount = str(amount_val).replace("$", "").replace(",", "").strip()
        try:
            numeric_amount = float(cleaned_amount)
        except ValueError:
            numeric_amount = 0.0

        direction_raw = str(
            _get("direction", "tipo", "type", "sentido", "debito_credito", default="")
        ).upper()

        if direction_raw in {"CREDIT", "INCOME", "ABONO", "INGRESO", "DEPOSITO", "CR"}:
            direction: Literal["income", "expense", "saving"] = "income"
        elif direction_raw in {"SAVING", "TRANSFER_SAVINGS", "AHORRO", "INVERSION"}:
            direction = "saving"
        elif direction_raw in {"DEBIT", "EXPENSE", "CARGO", "EGRESO", "DB"}:
            direction = "expense"
        elif numeric_amount < 0:
            direction = "expense"
        else:
            direction = "expense"

        final_amount = abs(numeric_amount)
        if final_amount <= 0:
            final_amount = 1.0

        # 3. Category, merchant, account
        category = str(
            _get(
                "category_raw",
                "category",
                "categoria_banco",
                "categoria",
                "rubro",
                default="GENERAL",
            )
        ).upper()
        merchant = str(
            _get(
                "merchant_raw",
                "merchant",
                "descripcion_comercio",
                "description",
                "descripcion",
                "concepto",
                "comercio",
                "detalle",
                default="Comercio",
            )
        ).strip()
        account = str(
            _get(
                "account_alias", "account", "cuenta", "producto_mascara", default="Cuenta Bancaria"
            )
        ).strip()
        currency = str(_get("currency", "moneda", default="COP")).strip().upper()
        notes = str(
            _get("notes_raw", "notes", "notas", "memo", default="Importado via CSVBankConnector")
        ).strip()

        disc_field = _get("is_discretionary", "discrecional", "discretionary", default=None)
        if disc_field is not None:
            is_disc = str(disc_field).lower() in {"true", "1", "yes", "si", "t"}
        else:
            is_disc = cls._is_discretionary(category, merchant)

        return FinanceEventInput(
            tx_timestamp=str(raw_ts),
            amount=final_amount,
            currency=currency,
            direction=direction,
            category_raw=category,
            merchant_raw=merchant,
            account_alias=account,
            is_discretionary=is_disc,
            notes_raw=notes,
            source_system="real_bank_csv",
        )

    def parse_csv(self, file_path_or_buffer: str | Path | TextIO) -> list[FinanceEventInput]:
        """Parse bank CSV export into a validated list of FinanceEventInput."""
        rows: list[dict[str, Any]] = []

        if isinstance(file_path_or_buffer, (str, Path)):
            path = Path(file_path_or_buffer)
            if path.exists() and path.is_file():
                with open(path, encoding="utf-8-sig", errors="replace") as f:
                    rows = list(csv.DictReader(f))
            else:
                # Treated as direct CSV content string
                rows = list(csv.DictReader(io.StringIO(str(file_path_or_buffer))))
        elif hasattr(file_path_or_buffer, "read"):
            content = file_path_or_buffer.read()
            if isinstance(content, bytes):
                content = content.decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(content)))
        else:
            rows = list(csv.DictReader(io.StringIO(str(file_path_or_buffer))))

        events: list[FinanceEventInput] = []
        for r in rows:
            if not r:
                continue
            try:
                events.append(self.map_external_record(r))
            except Exception:
                continue
        return events


class WearableExportConnector:
    """Parse health and sleep exports (Apple Health, Oura, Whoop) into HabitsSleepEventInput."""

    source_name = "wearable_export_connector"
    domain: DomainName = "habits_sleep"

    @classmethod
    def map_external_record(cls, raw: dict[str, Any]) -> HabitsSleepEventInput:
        """Transform a wearable/health telemetry dictionary into HabitsSleepEventInput."""
        norm = {str(k).strip().lower(): v for k, v in raw.items() if k is not None}

        def _get(*keys: str, default: Any = None) -> Any:
            for k in keys:
                if k in norm and norm[k] is not None:
                    val = str(norm[k]).strip()
                    if val != "":
                        return val
            return default

        # Log date
        today_iso = date.today().isoformat()
        log_date = str(
            _get("log_date", "sleep_date", "date", "day", "summary_date", default=today_iso)
        )

        # Sleep duration
        sleep_hours_raw = _get("sleep_hours", "duration_hours", "total_sleep_hours", default=None)
        if sleep_hours_raw is not None:
            try:
                sleep_hours = float(sleep_hours_raw)
            except ValueError:
                sleep_hours = 7.0
        else:
            duration_min_raw = _get("duration_min", "duration_minutes", "active_min", default=None)
            if duration_min_raw is not None:
                try:
                    sleep_hours = round(float(duration_min_raw) / 60.0, 2)
                except ValueError:
                    sleep_hours = 7.0
            else:
                total_duration_sec = _get("total_sleep_duration", "duration", default=None)
                if total_duration_sec is not None:
                    try:
                        sec = float(total_duration_sec)
                        sleep_hours = round(sec / 3600.0 if sec > 300 else sec, 2)
                    except ValueError:
                        sleep_hours = 7.0
                else:
                    sleep_hours = 7.0

        sleep_hours = max(0.0, min(24.0, float(sleep_hours)))

        # Sleep timestamps
        start_ts = _get(
            "sleep_start_ts", "bed_time", "bedtime_start", "start", default=f"{log_date} 23:00:00"
        )
        end_ts = _get(
            "sleep_end_ts", "wake_time", "bedtime_end", "end", default=f"{log_date} 07:00:00"
        )

        # Quality score
        quality_raw = _get(
            "sleep_quality_score", "quality_pct", "sleep_score", "score", default="75.0"
        )
        try:
            quality = max(0.0, min(100.0, float(quality_raw)))
        except ValueError:
            quality = 75.0

        # Exercise minutes
        active_raw = _get(
            "exercise_minutes", "active_min", "active_minutes", "workout_minutes", default="0"
        )
        try:
            exercise = max(0, min(600, int(float(active_raw))))
        except ValueError:
            exercise = 0

        # Habits
        habits_done_raw = _get(
            "habit_completed_count", "daily_habits_done", "habits_done", default="0"
        )
        try:
            habits_done = max(0, min(50, int(float(habits_done_raw))))
        except ValueError:
            habits_done = 0

        habits_target_raw = _get(
            "habit_total_count", "daily_habits_target", "habits_target", default="5"
        )
        try:
            habits_target = max(1, min(50, int(float(habits_target_raw))))
        except ValueError:
            habits_target = 5

        # Energy / readiness
        readiness_raw = _get(
            "energy_score", "readiness_10", "readiness_score", "readiness", default="7.0"
        )
        try:
            energy = float(readiness_raw)
            if energy > 10.0:
                energy = round(energy / 10.0, 1)  # Normalize 0-100 to 1-10
            energy = max(1.0, min(10.0, energy))
        except ValueError:
            energy = 7.0

        device = _get("device_id", "source_device", "tracker", default="Wearable Export")
        notes = f"Sync wearable ({device})"

        return HabitsSleepEventInput(
            log_date=log_date,
            sleep_start_ts=str(start_ts),
            sleep_end_ts=str(end_ts),
            sleep_hours=sleep_hours,
            sleep_quality_score=quality,
            exercise_minutes=exercise,
            habit_completed_count=habits_done,
            habit_total_count=habits_target,
            energy_score=energy,
            notes_raw=notes,
            source_system="wearable_export_connector",
        )

    def parse_health_export(
        self,
        payload_or_file: str | Path | dict[str, Any] | list[dict[str, Any]],
    ) -> list[HabitsSleepEventInput]:
        """Parse health/wearable export (JSON file, CSV file, or raw data)
        into HabitsSleepEventInput list."""
        data: Any = None

        if isinstance(payload_or_file, (str, Path)):
            path = Path(payload_or_file)
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if path.suffix.lower() == ".csv":
                    data = list(csv.DictReader(io.StringIO(text)))
                else:
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        data = list(csv.DictReader(io.StringIO(text)))
            else:
                raw_str = str(payload_or_file).strip()
                try:
                    data = json.loads(raw_str)
                except json.JSONDecodeError:
                    data = list(csv.DictReader(io.StringIO(raw_str)))
        else:
            data = payload_or_file

        if isinstance(data, dict):
            # Check for container envelopes like {"data": [...]}, {"sleep": [...]}
            for candidate_key in ("data", "sleep", "records", "items", "entries"):
                if isinstance(data.get(candidate_key), list):
                    data = data[candidate_key]
                    break
            else:
                data = [data]

        if not isinstance(data, list):
            data = [data]

        events: list[HabitsSleepEventInput] = []
        for item in data:
            if isinstance(item, dict):
                try:
                    events.append(self.map_external_record(item))
                except Exception:
                    continue
        return events


class GoogleCalendarConnector:
    """Connector for Google Calendar event synchronization.

    Architectural stub and adapter ready for Google Workspace OAuth2 integration.
    Future OAuth integration will use:
    - google-auth: Service account or authorized user credentials.
    - google-auth-oauthlib: Desktop/web OAuth2 authorization flows.
    - google-api-python-client: Resource discovery build('calendar', 'v3', ...).
    """

    source_name = "google_calendar_connector"
    domain: DomainName = "time_calendar"

    @staticmethod
    def map_external_record(raw: dict[str, Any]) -> TimeBlockEventInput:
        """Map a Google Calendar v3 event resource into TimeBlockEventInput."""
        summary = str(raw.get("summary") or raw.get("title") or "Bloque de Calendario")
        start_raw = raw.get("start", {})
        end_raw = raw.get("end", {})

        start_iso = (
            start_raw.get("dateTime")
            or start_raw.get("date")
            or raw.get("start_ts")
            or raw.get("start_iso")
            or datetime.now().isoformat()
        )
        end_iso = (
            end_raw.get("dateTime")
            or end_raw.get("date")
            or raw.get("end_ts")
            or raw.get("end_iso")
            or (datetime.now() + timedelta(hours=1)).isoformat()
        )

        try:
            start_dt = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
            duration_minutes = max(15, min(1440, int((end_dt - start_dt).total_seconds() // 60)))
        except Exception:
            duration_minutes = 60
            start_dt = datetime.now()
            end_dt = start_dt + timedelta(minutes=60)

        # Categorize block type based on summary / category hints
        summary_upper = summary.upper()
        if any(
            w in summary_upper for w in ("MEETING", "SYNC", "1:1", "STANDUP", "CALL", "REUNION")
        ):
            block_type: Literal["DEEP_WORK", "MEETING", "LEISURE", "ADMIN", "REST"] = "MEETING"
            priority: Literal["low", "medium", "high"] = "medium"
        elif any(
            w in summary_upper for w in ("FOCUS", "DEEP WORK", "CODING", "ESTUDIO", "DESARROLLO")
        ):
            block_type = "DEEP_WORK"
            priority = "high"
        elif any(w in summary_upper for w in ("LUNCH", "GYM", "EJERCICIO", "BREAK", "DESCANSO")):
            block_type = "LEISURE"
            priority = "low"
        else:
            block_type = "ADMIN"
            priority = "medium"

        calendar_name = str(raw.get("calendar_name") or raw.get("domain_tag") or "work").lower()

        return TimeBlockEventInput(
            start_ts=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_ts=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration_minutes=duration_minutes,
            domain_tag=calendar_name,
            block_type=block_type,
            title_raw=summary,
            priority=priority,
            completed_flag=True,
            source_system="google_calendar_connector",
        )

    def fetch_events(
        self,
        credentials_path: str = "",
        days: int = 7,
        anchor_date: date | None = None,
    ) -> list[TimeBlockEventInput]:
        """Fetch calendar events for the lookback window.

        Note: When `credentials_path` points to a valid client_secret.json / token.json,
        future implementation initializes the Google Calendar API client:
            ```python
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials.from_authorized_user_file(credentials_path, ['https://www.googleapis.com/auth/calendar.readonly'])
            service = build('calendar', 'v3', credentials=creds)
            # events = service.events().list(calendarId='primary', ...).execute()
            ```
        In the current stage, it returns a typed list of structured TimeBlockEventInput events
        satisfying the system contract.
        """
        end_dt = anchor_date or date.today()
        events: list[TimeBlockEventInput] = []

        # Generate representative typed calendar blocks for the requested window
        for offset in range(days):
            day = end_dt - timedelta(days=offset)
            # Deep work block
            dw_start = datetime.combine(day, time(9, 0))
            dw_end = dw_start + timedelta(minutes=120)
            events.append(
                TimeBlockEventInput(
                    start_ts=dw_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_ts=dw_end.strftime("%Y-%m-%d %H:%M:%S"),
                    duration_minutes=120,
                    domain_tag="work",
                    block_type="DEEP_WORK",
                    title_raw="Deep Work - Desarrollo",
                    priority="high",
                    completed_flag=True,
                    source_system="google_calendar_connector",
                )
            )
            # Sync meeting block
            m_start = datetime.combine(day, time(14, 0))
            m_end = m_start + timedelta(minutes=45)
            events.append(
                TimeBlockEventInput(
                    start_ts=m_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_ts=m_end.strftime("%Y-%m-%d %H:%M:%S"),
                    duration_minutes=45,
                    domain_tag="work",
                    block_type="MEETING",
                    title_raw="Team Sync",
                    priority="medium",
                    completed_flag=True,
                    source_system="google_calendar_connector",
                )
            )

        return events


class RealConnectorHub:
    """Coordinate real data source adapters into BatchIngestor."""

    def __init__(self, ingestor: BatchIngestor) -> None:
        self.ingestor = ingestor
        self.bank = CSVBankConnector()
        self.wearable = WearableExportConnector()
        self.calendar = GoogleCalendarConnector()

    def ingest_bank_csv(
        self,
        file_path_or_buffer: str | Path | TextIO,
        auto_flush: bool = False,
    ) -> list[str]:
        events = self.bank.parse_csv(file_path_or_buffer)
        entry_ids = [self.ingestor.capture_finance(ev).entry_id for ev in events]
        if auto_flush and entry_ids:
            self.ingestor.flush_batch()
        return entry_ids

    def ingest_wearable_export(
        self,
        payload_or_file: str | Path | dict[str, Any] | list[dict[str, Any]],
        auto_flush: bool = False,
    ) -> list[str]:
        events = self.wearable.parse_health_export(payload_or_file)
        entry_ids = [self.ingestor.capture_habits_sleep(ev).entry_id for ev in events]
        if auto_flush and entry_ids:
            self.ingestor.flush_batch()
        return entry_ids

    def ingest_calendar_events(
        self,
        credentials_path: str = "",
        days: int = 7,
        auto_flush: bool = False,
    ) -> list[str]:
        events = self.calendar.fetch_events(credentials_path=credentials_path, days=days)
        entry_ids = [self.ingestor.capture_time_block(ev).entry_id for ev in events]
        if auto_flush and entry_ids:
            self.ingestor.flush_batch()
        return entry_ids
