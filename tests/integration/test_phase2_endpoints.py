"""Integration tests for Phase 2 analytical, anomaly, simulation, and ask workflows."""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def _setup_test_client(tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=180,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )
    settings = Settings(_env_file=None, lup_sqlite_path=db_path, gemini_api_key="")
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    return TestClient(create_app(settings=settings, store=store, repo=repo))


def test_phase2_rest_endpoints_trends_anomalies_simulate(tmp_path: Path) -> None:
    client = _setup_test_client(tmp_path)

    trends_resp = client.get("/v1/trends?days=90")
    assert trends_resp.status_code == 200
    trends_data = trends_resp.json()
    assert trends_data["analysis"]["total_days"] == 90
    assert len(trends_data["analysis"]["trends"]) >= 8

    anom_resp = client.get("/v1/anomalies?days=90")
    assert anom_resp.status_code == 200
    anom_data = anom_resp.json()
    assert anom_data["scan"]["anomalies"]
    assert anom_data["recommendations"]

    sim_resp = client.post(
        "/v1/simulate",
        json={
            "baseline_days": 180,
            "scenario": {
                "horizon_days": 90,
                "discretionary_spend_delta_pct": -0.25,
                "sleep_hours_target": 7.5,
                "deep_work_delta_hours_per_week": 4.0,
                "iterations": 300,
                "seed": 42,
            },
        },
    )
    assert sim_resp.status_code == 200
    sim_data = sim_resp.json()
    sav = sim_data["simulation"]["trajectories"]["cumulative_savings"]
    assert sav["conservative_p25"] <= sav["base_p50"] <= sav["optimistic_p75"]
    assert sim_data["recommendations"][0]["evidence"]


def test_phase2_ask_routes_simulation_anomalies_and_trends(tmp_path: Path) -> None:
    client = _setup_test_client(tmp_path)

    sim_ask = client.post(
        "/v1/ask",
        json={"question": "Simula que pasa si reduzco gastos de ocio 25% y duermo 7.5h en 4 meses"},
    )
    assert sim_ask.status_code == 200
    sim_body = sim_ask.json()
    assert sim_body["analysis_type"] == "simulation"
    assert sim_body["simulation"]["horizon_days"] == 120
    assert sim_body["simulation"]["scenario"]["discretionary_spend_delta_pct"] == -0.25
    assert sim_body["simulation"]["scenario"]["sleep_hours_target"] == 7.5
    assert sim_body["recommendations"]

    anom_ask = client.post(
        "/v1/ask",
        json={"question": "Detecta anomalias y oportunidades en los ultimos 90 dias"},
    )
    assert anom_ask.status_code == 200
    anom_body = anom_ask.json()
    assert anom_body["analysis_type"] == "anomalies"
    assert anom_body["anomaly_scan"]["anomalies"]

    trend_ask = client.post(
        "/v1/ask",
        json={"question": "Cuales son mis tendencias y correlaciones de los ultimos 90 dias?"},
    )
    assert trend_ask.status_code == 200
    trend_body = trend_ask.json()
    assert trend_body["analysis_type"] == "trends"
    assert trend_body["trends"]["correlations"]
