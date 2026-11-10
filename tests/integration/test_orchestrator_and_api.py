"""Integration tests for LupOrchestrator and FastAPI endpoints under masked_cloud."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.llm.gemini_client import IntentPlan, SynthesizedAnswer
from lup_ai.privacy.text_anonymizer import TextAnonymizer
from lup_ai.seeds.generate_synthetic_data import generate_dataset

T = TypeVar("T", bound=BaseModel)


class StubGeminiClient:
    """Deterministic stub recording every sanitized payload sent to Cloud LLM."""

    def __init__(self, force_unsafe_sql: bool = False) -> None:
        self.recorded_payloads: list[dict[str, Any]] = []
        self._force_unsafe_sql = force_unsafe_sql

    @property
    def model_name(self) -> str:
        return "gemini-3.8-flash-stub"

    def is_configured(self) -> bool:
        return True

    def generate_structured(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[T],
    ) -> T:
        TextAnonymizer.assert_no_pii(user_payload)
        self.recorded_payloads.append(user_payload)

        if schema is IntentPlan:
            if self._force_unsafe_sql:
                return schema.model_validate(
                    {
                        "domain": "finance",
                        "mode": "semantic_sql",
                        "days": 30,
                        "semantic_sql": "DROP TABLE RAW.RAW_FINANCE_TX",
                    }
                )
            return schema.model_validate(
                {
                    "domain": "cross_domain",
                    "mode": "catalog",
                    "days": 30,
                }
            )

        if schema is SynthesizedAnswer:
            agg = user_payload.get("aggregates", {})
            q_text = user_payload.get("question", "")
            return schema.model_validate(
                {
                    "answer": f"Analisis sobre '{q_text}': gasto={agg.get('total_spend', 0)}.",
                    "key_observations": [f"Vista consultada: {user_payload.get('source_view')}"],
                    "goal_alignment_note": "Alineado con objetivos activos.",
                }
            )

        raise AssertionError(f"Unexpected schema {schema}")


def test_api_end_to_end_masked_cloud_and_pii_isolation(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(output_dir=data_dir, days=30, seed=12, sqlite_path=db_path)

    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        gemini_api_key="",
    )
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    stub_llm = StubGeminiClient()

    app = create_app(settings=settings, store=store, repo=repo, llm_client=stub_llm)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["privacy_mode"] == "masked_cloud"
    assert health.json()["data_backend"] == "local_synthetic_csv"

    goals = client.get("/v1/goals")
    assert goals.status_code == 200
    assert len(goals.json()) == 4

    prefs = client.get("/v1/preferences")
    assert prefs.status_code == 200
    assert len(prefs.json()) == 3

    cat_resp = client.get("/v1/catalog/finance?days=30")
    assert cat_resp.status_code == 200
    assert cat_resp.json()["aggregates"]["total_spend"] > 0

    ask_resp = client.post(
        "/v1/ask",
        json={
            "session_id": "sess-api-1",
            "question": (
                "Analiza mis gastos con Juan Perez de los ultimos 30 dias, "
                "cuenta ****4821 y correo juan@demo.com."
            ),
        },
    )
    assert ask_resp.status_code == 200
    body = ask_resp.json()
    assert body["privacy_mode"] == "masked_cloud"
    assert body["model_used"] == "gemini-3.8-flash-stub"
    assert body["domain"] == "finance"
    assert body["evidence"]["source_view"] == "SEMANTIC.VW_FINANCE_SUMMARY"

    # Confirm zero raw PII leaked into any LLM payload, while local response re-hydrates cleanly.
    serialized_llm = str(stub_llm.recorded_payloads)
    assert "Juan Perez" not in serialized_llm
    assert "****4821" not in serialized_llm
    assert "juan@demo.com" not in serialized_llm
    assert "Juan Perez" in body["answer"]


def test_api_offline_fallback_and_sql_guard_recovery(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir, days=30, seed=15, end_date=date(2026, 11, 10), sqlite_path=db_path
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path, gemini_api_key="")
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)

    # 1. No Gemini key configured -> deterministic catalog summary fallback.
    app_offline = create_app(settings=settings, store=store, repo=repo)
    client_offline = TestClient(app_offline)
    resp = client_offline.post("/v1/ask", json={"question": "Como va mi sueno esta semana?"})
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "deterministic_catalog"
    assert resp.json()["domain"] == "habits_sleep"

    # 2. Unsafe SQL planned by LLM -> SQL Guard rejects it and orchestrator falls back safely.
    unsafe_stub = StubGeminiClient(force_unsafe_sql=True)
    app_guarded = create_app(settings=settings, store=store, repo=repo, llm_client=unsafe_stub)
    client_guarded = TestClient(app_guarded)
    resp2 = client_guarded.post("/v1/ask", json={"question": "Dame una vista rara del sistema"})
    assert resp2.status_code == 200
    assert resp2.json()["evidence"]["source_view"] == "SEMANTIC.VW_FINANCE_SUMMARY"
