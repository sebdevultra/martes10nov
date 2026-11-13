"""Integration tests for Phase 4 simulated-sync API, LupApiClient, and Streamlit UI."""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset
from lup_ai.ui.api_client import LupApiClient
from lup_ai.ui.streamlit_app import get_app_path


def test_phase4_simulated_connectors_and_streamlit_dashboard(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=90,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path, gemini_api_key="")
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    fastapi_client = TestClient(create_app(settings=settings, store=store, repo=repo))

    # 1. Trigger simulated external connectors sync via REST API.
    sync_resp = fastapi_client.post(
        "/v1/connectors/simulate-sync",
        json={"days": 2, "seed": 202, "auto_flush": True},
    )
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    assert sync_data["source_mode"] == "simulated_external_feeds"
    assert sync_data["events_captured"] == 8
    assert sync_data["flush_result"]["flushed_count"] == 8

    # 2. Verify LupApiClient works cleanly against FastAPI.
    api_client = LupApiClient(client=fastapi_client)
    assert api_client.health()["privacy_mode"] == "masked_cloud"
    assert len(api_client.get_goals()) == 4

    # 3. Render Streamlit dashboard headlessly using AppTest with injected LupApiClient.
    at = AppTest.from_file(str(get_app_path()), default_timeout=15)
    at.session_state["lup_api_client"] = api_client
    at.run()

    assert not at.exception
    assert any("LUP AI" in t.value for t in at.title)
    assert len(at.metric) >= 4


def test_real_connectors_manual_ingest_endpoints(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=30,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path, gemini_api_key="")
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    client = TestClient(create_app(settings=settings, store=store, repo=repo))

    # 1. Ingest Bank CSV via UploadFile
    csv_bytes = b"Fecha,Monto,Tipo,Comercio\n2026-11-12 11:00:00,45000,DEBIT,Restaurante Local\n"
    bank_resp = client.post(
        "/v1/ingest/bank/csv?auto_flush=true",
        files={"file": ("movimientos.csv", csv_bytes, "text/csv")},
    )
    assert bank_resp.status_code == 200
    bank_data = bank_resp.json()
    assert bank_data["domain"] == "finance"
    assert bank_data["events_captured"] == 1
    assert bank_data["flushed"] is True
    assert bank_data["flush_result"]["flushed_count"] == 1

    # 2. Ingest Wearable export via JSON upload
    json_bytes = b'[{"sleep_date":"2026-11-12","duration_min":450,"quality_pct":85.0}]'
    wear_resp = client.post(
        "/v1/ingest/wearable/health?auto_flush=true",
        files={"file": ("oura_export.json", json_bytes, "application/json")},
    )
    assert wear_resp.status_code == 200
    wear_data = wear_resp.json()
    assert wear_data["domain"] == "habits_sleep"
    assert wear_data["events_captured"] == 1

    # 3. Ingest Calendar events stub
    cal_resp = client.post("/v1/ingest/calendar/events?days=2&auto_flush=true")
    assert cal_resp.status_code == 200
    cal_data = cal_resp.json()
    assert cal_data["domain"] == "time_calendar"
    assert cal_data["events_captured"] == 4
