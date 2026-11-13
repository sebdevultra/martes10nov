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


def test_goals_and_preferences_crud_endpoints(tmp_path: Path) -> None:
    client = _setup_test_client(tmp_path)

    # 1. Goals CRUD
    initial_goals = client.get("/v1/goals").json()
    len(initial_goals)

    create_resp = client.post(
        "/v1/goals",
        json={
            "domain": "finance",
            "title": "Ahorro para Emergencias",
            "metric_name": "emergency_fund",
            "target_value": 15000000.0,
            "unit": "COP",
            "comparison_operator": ">=",
            "priority": 1,
            "status": "active",
        },
    )
    assert create_resp.status_code == 201
    created_goal = create_resp.json()
    gid = created_goal["goal_id"]
    assert gid.startswith("goal_finance_")
    assert created_goal["title"] == "Ahorro para Emergencias"
    assert created_goal["target_value"] == 15000000.0

    # Get by ID
    get_resp = client.get(f"/v1/goals/{gid}")
    assert get_resp.status_code == 200
    assert get_resp.json()["goal_id"] == gid

    # Update goal
    put_resp = client.put(
        f"/v1/goals/{gid}",
        json={"target_value": 20000000.0, "title": "Ahorro Emergencias Ampliado"},
    )
    assert put_resp.status_code == 200
    updated_goal = put_resp.json()
    assert updated_goal["target_value"] == 20000000.0
    assert updated_goal["title"] == "Ahorro Emergencias Ampliado"

    # Delete goal
    del_resp = client.delete(f"/v1/goals/{gid}")
    assert del_resp.status_code == 204

    # Verify deleted
    assert client.get(f"/v1/goals/{gid}").status_code == 404
    assert client.put(f"/v1/goals/{gid}", json={"title": "No existe"}).status_code == 404
    assert client.delete(f"/v1/goals/{gid}").status_code == 404

    # 2. Preferences CRUD
    create_p_resp = client.post(
        "/v1/preferences",
        json={
            "domain": "habits_sleep",
            "key_name": "weekend_sleep_min",
            "value_json": '{"min_hours": 8.5}',
            "is_hard_constraint": True,
        },
    )
    assert create_p_resp.status_code == 201
    created_pref = create_p_resp.json()
    pid = created_pref["pref_id"]
    assert pid == "pref_habits_sleep_weekend_sleep_min"
    assert created_pref["is_hard_constraint"] is True

    # Get by ID
    get_p_resp = client.get(f"/v1/preferences/{pid}")
    assert get_p_resp.status_code == 200
    assert get_p_resp.json()["pref_id"] == pid

    # Update preference
    put_p_resp = client.put(
        f"/v1/preferences/{pid}",
        json={"is_hard_constraint": False, "value_json": '{"min_hours": 9.0}'},
    )
    assert put_p_resp.status_code == 200
    updated_pref = put_p_resp.json()
    assert updated_pref["is_hard_constraint"] is False
    assert "9.0" in updated_pref["value_json"]

    # Delete preference
    del_p_resp = client.delete(f"/v1/preferences/{pid}")
    assert del_p_resp.status_code == 204

    # Verify deleted
    assert client.get(f"/v1/preferences/{pid}").status_code == 404
    assert client.put(f"/v1/preferences/{pid}", json={"key_name": "x"}).status_code == 404
    assert client.delete(f"/v1/preferences/{pid}").status_code == 404
