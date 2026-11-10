"""Gemini 3.8 cloud client with strict masked_cloud PII assertion and Pydantic outputs."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from lup_ai.config.settings import Settings
from lup_ai.engines.simulation_engine import SimulationScenario
from lup_ai.privacy.text_anonymizer import TextAnonymizer

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini API credentials are missing or invalid."""


class IntentPlan(BaseModel):
    """Structured routing decision emitted by Gemini 3.8 over sanitized input."""

    domain: Literal["finance", "habits_sleep", "time_calendar", "cross_domain", "goals"]
    mode: Literal["catalog", "semantic_sql"] = "catalog"
    analysis_type: Literal["summary", "trends", "anomalies", "simulation"] = "summary"
    days: int = Field(default=30, ge=1, le=365)
    direction: Literal["income", "expense", "saving"] | None = None
    category: str | None = None
    discretionary_only: bool = False
    domain_tag: str | None = None
    block_type: str | None = None
    semantic_sql: str | None = None
    simulation_params: SimulationScenario | None = None
    reasoning: str = ""


class SynthesizedAnswer(BaseModel):
    """Grounded natural-language synthesis over deterministic aggregates."""

    answer: str
    key_observations: list[str] = Field(default_factory=list)
    goal_alignment_note: str = ""


class LLMClient(Protocol):
    """Protocol allowing clean deterministic stubs in unit and integration tests."""

    @property
    def model_name(self) -> str:
        """Return configured cloud model identifier."""

    def is_configured(self) -> bool:
        """Return True if the LLM provider can accept requests."""

    def generate_structured(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[SchemaT],
    ) -> SchemaT:
        """Generate and validate a Pydantic-structured JSON response."""


class GeminiClient:
    """Google Gemini 3.8 client guarded by pre-call masked_cloud PII assertions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    @property
    def model_name(self) -> str:
        return self._settings.gemini_model

    def is_configured(self) -> bool:
        key = self._settings.gemini_api_key.strip()
        return bool(key and key != "your_gemini_api_key_here")

    def _get_client(self) -> Any:
        if not self.is_configured():
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        if self._client is None:
            from google import genai
            from google.genai import types

            timeout_ms = self._settings.gemini_timeout_seconds * 1000
            self._client = genai.Client(
                api_key=self._settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
        return self._client

    def generate_structured(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema: type[SchemaT],
    ) -> SchemaT:
        """Enforce PII guardrail and obtain validated Pydantic output with up to 2 retries."""
        TextAnonymizer.assert_no_pii(user_payload)

        from google.genai import types

        client = self._get_client()
        prompt_text = json.dumps(user_payload, ensure_ascii=False, default=str)
        last_error: Exception | None = None

        for attempt in range(3):
            extra_hint = (
                ""
                if attempt == 0
                else (
                    f"\nPrevious validation error: {last_error}. Return valid JSON matching schema."
                )
            )
            response = client.models.generate_content(
                model=self._settings.gemini_model,
                contents=f"{prompt_text}{extra_hint}",
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
            raw_text = (response.text or "").strip()
            try:
                return schema.model_validate_json(raw_text)
            except ValidationError as exc:
                last_error = exc

        raise ValueError(f"Gemini output failed schema validation after retries: {last_error}")
