"""Integration tests for Phase 3 proactive scanning, feedback loop, and batch ingest API."""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_phase3_proactive_feedback_and_ingest_endpoints(tmp_path: Path) -> None:
    today = date.today()
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=180,
        seed=42,
        end_date=today,
        sqlite_path=db_path,
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path, gemini_api_key="")
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    client = TestClient(create_app(settings=settings, store=store, repo=repo))

    # 1. Trigger proactive scan -> populates pending decision inbox.
    scan_resp = client.post("/v1/jobs/proactive-scan", json={"days": 180})
    assert scan_resp.status_code == 200
    scan_data = scan_resp.json()
    assert scan_data["anomalies_found"] > 0
    assert scan_data["persisted_decision_ids"]

    decisions_resp = client.get("/v1/decisions?status=pending")
    assert decisions_resp.status_code == 200
    pending_list = decisions_resp.json()
    assert len(pending_list) == len(scan_data["persisted_decision_ids"])

    # 2. Submit user feedback -> accept first decision, dismiss second if present.
    first_id = pending_list[0]["decision_id"]
    fb_resp = client.post(
        f"/v1/decisions/{first_id}/feedback",
        json={"user_action": "accepted", "user_feedback": "Aplicando limite"},
    )
    assert fb_resp.status_code == 200
    assert fb_resp.json()["user_action"] == "accepted"

    # Unknown decision returns 404.
    missing_resp = client.post(
        "/v1/decisions/nonexistent_id/feedback",
        json={"user_action": "dismissed"},
    )
    assert missing_resp.status_code == 404

    # 3. Force outcome evaluation via proactive scan -> produces evaluated outcome.
    eval_scan = client.post(
        "/v1/jobs/proactive-scan",
        json={"days": 180, "evaluate_outcomes_force": True},
    )
    assert eval_scan.status_code == 200
    assert len(eval_scan.json()["evaluated_outcomes"]) >= 1

    outcomes_resp = client.get("/v1/decisions/outcomes")
    assert outcomes_resp.status_code == 200
    outcomes_body = outcomes_resp.json()
    assert outcomes_body["adherence_summary"]["evaluated_outcomes"] >= 1
    assert outcomes_body["outcomes"]

    # 4. Incremental capture -> pending queue -> batch flush -> visible in catalog.
    cap_resp = client.post(
        "/v1/ingest/capture",
        json={
            "domain": "finance",
            "payload": {
                "tx_timestamp": "2026-11-10 13:00:00",
                "amount": 42000.0,
                "direction": "expense",
                "category_raw": "PHASE3_SMOKE_CAT",
                "is_discretionary": True,
            },
        },
    )
    assert cap_resp.status_code == 200
    assert cap_resp.json()["sync_status"] == "pending"

    pending_resp = client.get("/v1/ingest/pending")
    assert pending_resp.status_code == 200
    assert pending_resp.json()["pending_count"] == 1

    flush_resp = client.post("/v1/ingest/flush")
    assert flush_resp.status_code == 200
    assert flush_resp.json()["flushed_count"] == 1
    assert flush_resp.json()["decisions_synced"] == 0
    assert flush_resp.json()["outcomes_synced"] == 0

    cat_resp = client.get("/v1/catalog/finance?days=7&category=PHASE3_SMOKE_CAT")
    assert cat_resp.status_code == 200
    assert cat_resp.json()["aggregates"]["total_spend"] == 42000.0

    sched_resp = client.get("/v1/jobs/scheduler")
    assert sched_resp.status_code == 200
    assert sched_resp.json()["proactive_scan_interval_minutes"] == 720
