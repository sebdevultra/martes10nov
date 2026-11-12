"""Unit tests for GeminiClient retries, model fallback, and PII guardrail."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from lup_ai.config.settings import Settings
from lup_ai.llm.gemini_client import GeminiClient, SynthesizedAnswer
from lup_ai.privacy.text_anonymizer import PrivacyViolationError


class _FakeModels:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[str] = []

    def generate_content(self, model: str, contents: str, config: Any) -> Any:
        self.calls.append(model)
        return self._handler(model, contents, config)


class _Transient503Error(RuntimeError):
    code = 503


def test_gemini_client_retries_transient_503_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(model: str, contents: str, config: Any) -> Any:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise _Transient503Error("503 UNAVAILABLE: high demand")
        return SimpleNamespace(
            text='{"answer": "OK", "key_observations": ["obs"], "goal_alignment_note": "ok"}'
        )

    cfg = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_model="gemini-3.5-flash-lite",
        gemini_fallback_models="gemini-3.5-flash",
    )
    client = GeminiClient(cfg, sleep_fn=lambda _: None)
    fake_models = _FakeModels(handler)
    client._client = SimpleNamespace(models=fake_models)

    res = client.generate_structured("sys", {"total_spend": 100}, SynthesizedAnswer)
    assert res.answer == "OK"
    assert client.last_used_model == "gemini-3.5-flash-lite"
    assert fake_models.calls == ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]


def test_gemini_client_falls_back_when_primary_model_remains_unavailable() -> None:
    def handler(model: str, contents: str, config: Any) -> Any:
        if model == "gemini-3.5-flash-lite":
            raise _Transient503Error("503 UNAVAILABLE: high demand")
        return SimpleNamespace(
            text=(
                '{"answer": "Fallback model OK", '
                '"key_observations": [], "goal_alignment_note": ""}'
            )
        )

    cfg = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_model="gemini-3.5-flash-lite",
        gemini_fallback_models="gemini-3.5-flash,gemini-3.6-flash",
    )
    client = GeminiClient(cfg, sleep_fn=lambda _: None)
    fake_models = _FakeModels(handler)
    client._client = SimpleNamespace(models=fake_models)

    res = client.generate_structured("sys", {"total_spend": 100}, SynthesizedAnswer)
    assert res.answer == "Fallback model OK"
    assert client.last_used_model == "gemini-3.5-flash"
    assert fake_models.calls == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]


def test_gemini_client_blocks_unmasked_pii_before_network_call() -> None:
    cfg = Settings(_env_file=None, gemini_api_key="test-key")
    client = GeminiClient(cfg, sleep_fn=lambda _: None)
    fake_models = _FakeModels(lambda *_: None)
    client._client = SimpleNamespace(models=fake_models)

    with pytest.raises(PrivacyViolationError):
        client.generate_structured("sys", {"email": "user@example.com"}, SynthesizedAnswer)
    assert fake_models.calls == []
