"""Unit tests for real external source connectors (Bank CSV, Wearable Export, Calendar)."""

from datetime import date
from pathlib import Path

from lup_ai.config.settings import Settings
from lup_ai.connectors.real_sources import (
    CSVBankConnector,
    GoogleCalendarConnector,
    RealConnectorHub,
    WearableExportConnector,
)
from lup_ai.data.batch_ingestor import BatchIngestor
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_csv_bank_connector_parsing(tmp_path: Path) -> None:
    csv_content = """Fecha,Monto,Tipo,Comercio,Categoria,Cuenta,Moneda
2026-11-12 10:30:00,52000,DEBIT,Supermercado Exito,GROCERIES,Ahorros ****1234,COP
2026-11-12 13:45:00,34000,DEBIT,Rappi Domicilios,DELIVERY,Ahorros ****1234,COP
2026-11-12 18:00:00,3500000,CREDIT,Nomina Empresa,SALARY,Corriente ****5678,COP
"""
    csv_file = tmp_path / "bank_export.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    connector = CSVBankConnector()
    assert connector.domain == "finance"

    events = connector.parse_csv(csv_file)
    assert len(events) == 3

    # Expense 1: Groceries
    assert events[0].amount == 52000.0
    assert events[0].direction == "expense"
    assert events[0].merchant_raw == "Supermercado Exito"
    assert events[0].category_raw == "GROCERIES"
    assert events[0].is_discretionary is False

    # Expense 2: Delivery (discretionary)
    assert events[1].amount == 34000.0
    assert events[1].direction == "expense"
    assert events[1].is_discretionary is True

    # Income: Salary
    assert events[2].amount == 3500000.0
    assert events[2].direction == "income"


def test_wearable_export_connector_parsing(tmp_path: Path) -> None:
    json_content = """[
        {
            "sleep_date": "2026-11-12",
            "bed_time": "2026-11-11 23:15:00",
            "wake_time": "2026-11-12 07:15:00",
            "duration_min": 480,
            "quality_pct": 88.0,
            "active_min": 45,
            "daily_habits_done": 5,
            "daily_habits_target": 5,
            "readiness_10": 8.5,
            "device_id": "OURA_RING_GEN3"
        }
    ]"""
    json_file = tmp_path / "health_export.json"
    json_file.write_text(json_content, encoding="utf-8")

    connector = WearableExportConnector()
    assert connector.domain == "habits_sleep"

    events = connector.parse_health_export(json_file)
    assert len(events) == 1
    assert events[0].sleep_hours == 8.0
    assert events[0].sleep_quality_score == 88.0
    assert events[0].exercise_minutes == 45
    assert events[0].habit_completed_count == 5
    assert events[0].energy_score == 8.5


def test_google_calendar_connector_stub() -> None:
    connector = GoogleCalendarConnector()
    assert connector.domain == "time_calendar"

    events = connector.fetch_events(credentials_path="dummy_path.json", days=3)
    assert len(events) == 6  # 2 events per day * 3 days
    for ev in events:
        assert ev.domain_tag == "work"
        assert ev.block_type in {"DEEP_WORK", "MEETING"}
        assert ev.duration_minutes > 0


def test_real_connector_hub_ingest_and_flush(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=15,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    ingestor = BatchIngestor(settings=settings, store=store, local_csv_repo=repo)

    hub = RealConnectorHub(ingestor=ingestor)

    # Ingest Bank CSV
    csv_raw = "Fecha,Monto,Tipo,Comercio\n2026-11-12 12:00:00,45000,DEBIT,Restaurante Centro\n"
    bank_ids = hub.ingest_bank_csv(csv_raw, auto_flush=False)
    assert len(bank_ids) == 1

    # Ingest Wearable
    wearable_raw = [{"sleep_date": "2026-11-12", "duration_min": 420, "quality_pct": 80.0}]
    wear_ids = hub.ingest_wearable_export(wearable_raw, auto_flush=False)
    assert len(wear_ids) == 1

    # Ingest Calendar
    cal_ids = hub.ingest_calendar_events(days=1, auto_flush=True)
    assert len(cal_ids) == 2

    # Since auto_flush=True was called on calendar, pending buffer was flushed
    assert store.list_pending_ingest_events() == []
