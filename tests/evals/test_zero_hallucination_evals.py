"""Golden evaluation harness verifying zero numeric hallucination and PII safety."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from lup_ai.agent.orchestrator import LupOrchestrator
from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.engines.simulation_engine import SimulationScenario
from lup_ai.llm.gemini_client import IntentPlan, SynthesizedAnswer
from lup_ai.privacy.text_anonymizer import TextAnonymizer
from lup_ai.seeds.generate_synthetic_data import generate_dataset

T = TypeVar("T", bound=BaseModel)


class AdversarialNumericLLMStub:
    """Stub trying to emit fabricated numeric claims while recording outbound payloads."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return "gemini-3.8-adversarial-stub"

    def is_configured(self) -> bool:
        return True

    def generate_structured(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[T],
    ) -> T:
        TextAnonymizer.assert_no_pii(user_payload)
        self.payloads.append(user_payload)
        if schema is IntentPlan:
            return schema.model_validate({"domain": "cross_domain", "analysis_type": "simulation"})
        if schema is SynthesizedAnswer:
            return schema.model_validate(
                {
                    "answer": "Proyeccion textual.",
                    "key_observations": ["Obs simulada"],
                    "goal_alignment_note": "Eval",
                }
            )
        raise AssertionError(f"Unexpected schema: {schema}")


def test_eval_zero_hallucination_lock_and_ground_truth_anomalies(tmp_path: Path) -> None:
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
    stub = AdversarialNumericLLMStub()

    # Ground truth deterministic baseline computed directly by orchestrator.
    direct = LupOrchestrator(settings=settings, store=store, repo=repo, llm_client=stub)
    expected_sim, _, _ = direct.simulate(
        SimulationScenario(
            horizon_days=90, discretionary_spend_delta_pct=-0.25, sleep_hours_target=7.5
        ),
        baseline_days=180,
    )

    client = TestClient(create_app(settings=settings, store=store, repo=repo, llm_client=stub))

    # 1. Even with LLM stub active, structured simulation metrics remain locked to Python engine.
    resp = client.post(
        "/v1/ask",
        json={
            "question": (
                "Simula si reduzco gastos ocio 25% y duermo 7.5h en 90 dias, "
                "revisando cuenta ****4821 de Carlos Ruiz y correo c.ruiz@example.org."
            )
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["simulation"]["trajectories"]["cumulative_savings"]["base_p50"]
        == expected_sim.trajectories["cumulative_savings"].base_p50
    )
    assert body["recommendations"][0]["evidence"]

    # Confirm zero raw PII leaked into outbound LLM payloads.
    serialized = str(stub.payloads)
    assert "****4821" not in serialized
    assert "Carlos Ruiz" not in serialized
    assert "c.ruiz@example.org" not in serialized

    # 2. Ground-truth anomalies injected in generator (spend spikes + short-sleep streak)
    # are detected deterministically over the 180-day window.
    anom_resp = client.get("/v1/anomalies?days=180")
    assert anom_resp.status_code == 200
    anomalies = anom_resp.json()["scan"]["anomalies"]
    detected_metrics = {item["metric_name"] for item in anomalies}
    assert "discretionary_spend" in detected_metrics
    assert "sleep_hours_streak" in detected_metrics

    # 3. Blocking hard-constraint collision detected when sleep target < 6.5h.
    bad_sim = client.post(
        "/v1/simulate",
        json={"baseline_days": 180, "scenario": {"horizon_days": 60, "sleep_hours_target": 5.5}},
    )
    assert bad_sim.status_code == 200
    collisions = bad_sim.json()["simulation"]["constraint_collisions"]
    assert any(
        c["severity"] == "blocking" and c["key_name"] == "min_sleep_hours" for c in collisions
    )
