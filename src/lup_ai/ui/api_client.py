"""HTTP client decoupling the Streamlit UI from internal LUP AI services."""

from __future__ import annotations

from typing import Any

import httpx


class LupApiError(RuntimeError):
    """Raised when the FastAPI backend returns an error or is unreachable."""


class LupApiClient:
    """Typed REST client targeting LUP AI FastAPI endpoints."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._external_client = client

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            if self._external_client is not None:
                resp = self._external_client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                )
            else:
                with httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
                    resp = client.request(
                        method,
                        path,
                        params=params,
                        json=json_body,
                    )
        except httpx.HTTPError as exc:
            raise LupApiError(f"Error de conexion con LUP AI API ({self.base_url}): {exc}") from exc

        if resp.status_code >= 400:
            raise LupApiError(f"API HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def get_goals(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/goals")

    def get_preferences(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/preferences")

    def get_catalog(self, domain: str, days: int = 30) -> dict[str, Any]:
        return self._request("GET", f"/v1/catalog/{domain}", params={"days": days})

    def get_trends(self, days: int = 90) -> dict[str, Any]:
        return self._request("GET", "/v1/trends", params={"days": days})

    def get_anomalies(self, days: int = 90) -> dict[str, Any]:
        return self._request("GET", "/v1/anomalies", params={"days": days})

    def simulate(self, scenario: dict[str, Any], baseline_days: int = 180) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/simulate",
            json_body={"scenario": scenario, "baseline_days": baseline_days},
        )

    def ask(self, question: str, session_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/ask",
            json_body={"question": question, "session_id": session_id},
        )

    def get_decisions(self, status: str | None = None) -> list[dict[str, Any]]:
        params = {"status": status} if status else None
        return self._request("GET", "/v1/decisions", params=params)

    def send_decision_feedback(
        self,
        decision_id: str,
        user_action: str,
        user_feedback: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/decisions/{decision_id}/feedback",
            json_body={"user_action": user_action, "user_feedback": user_feedback},
        )

    def get_outcomes(self) -> dict[str, Any]:
        return self._request("GET", "/v1/decisions/outcomes")

    def run_proactive_scan(
        self, days: int = 90, evaluate_outcomes_force: bool = False
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/jobs/proactive-scan",
            json_body={"days": days, "evaluate_outcomes_force": evaluate_outcomes_force},
        )

    def get_pending_ingest(self) -> dict[str, Any]:
        return self._request("GET", "/v1/ingest/pending")

    def capture_event(self, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/ingest/capture",
            json_body={"domain": domain, "payload": payload},
        )

    def flush_ingest(self) -> dict[str, Any]:
        return self._request("POST", "/v1/ingest/flush")

    def sync_simulated_sources(
        self,
        days: int = 3,
        seed: int = 101,
        auto_flush: bool = True,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"days": days, "seed": seed, "auto_flush": auto_flush}
        if sources:
            body["sources"] = sources
        return self._request("POST", "/v1/connectors/simulate-sync", json_body=body)

