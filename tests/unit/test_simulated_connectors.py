"""Unit tests for simulated external source connectors (Bank, Wearable, Calendar)."""

from datetime import date
from pathlib import Path

from lup_ai.config.settings import Settings
from lup_ai.connectors.simulated_sources import (
    SimulatedBankConnector,
    SimulatedCalendarConnector,
    SimulatedConnectorHub,
    SimulatedWearableConnector,
)
from lup_ai.data.batch_ingestor import BatchIngestor
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_simulated_connector_mappers_and_hub_sync(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=20,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    bank_event = SimulatedBankConnector.map_external_record(
        {
            "posted_at": "2026-11-10 15:00:00",
            "valor_cop": 48000,
            "tipo": "DEBIT",
            "categoria_banco": "DELIVERY",
            "descripcion_comercio": "Domicilios Centro",
        }
    )
    assert bank_event.amount == 48000.0
    assert bank_event.direction == "expense"
    assert bank_event.is_discretionary is True

    wear_event = SimulatedWearableConnector.map_external_record(
        {
            "sleep_date": "2026-11-10",
            "bed_time": "2026-11-09 23:00:00",
            "wake_time": "2026-11-10 06:30:00",
            "duration_min": 450,
            "quality_pct": 85.0,
            "active_min": 30,
            "daily_habits_done": 4,
            "readiness_10": 8.0,
        }
    )
    assert wear_event.sleep_hours == 7.5

    cal_event = SimulatedCalendarConnector.map_external_record(
        {
            "start_iso": "2026-11-10T08:00:00",
            "end_iso": "2026-11-10T10:00:00",
            "category_hint": "DEEP_WORK",
            "calendar_name": "work",
            "summary": "Arquitectura",
            "done": True,
        }
    )
    assert cal_event.duration_minutes == 120
    assert cal_event.block_type == "DEEP_WORK"

    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    ingestor = BatchIngestor(settings=settings, store=store, local_csv_repo=repo)
    hub = SimulatedConnectorHub(ingestor)

    report = hub.sync_all(days=2, seed=101, auto_flush=True, anchor_date=date(2026, 11, 10))
    assert report.events_captured == 8
    assert report.by_domain == {"finance": 2, "habits_sleep": 2, "time_calendar": 4}
    assert report.flushed is True
    assert report.flush_result is not None
    assert report.flush_result.flushed_count == 8
