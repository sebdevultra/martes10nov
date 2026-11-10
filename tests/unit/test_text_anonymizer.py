"""Unit tests for deterministic pre-LLM PII anonymizer."""

from pathlib import Path

import pytest

from lup_ai.data.local_store import LocalStore
from lup_ai.privacy.text_anonymizer import PrivacyViolationError, TextAnonymizer


def test_anonymize_and_rehydrate_preserves_session_consistency(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "local.db")
    anonymizer = TextAnonymizer(store=store, use_presidio=False)

    raw_text = (
        "Pago con Juan Perez en Comercio Sol SAS, correo usuario.demo@example.local, "
        "tel +57 300 555 1234, cuenta ****4821 y CC 10203040."
    )
    res = anonymizer.anonymize(
        raw_text,
        session_id="s-1",
        extra_sensitive_terms=["Comercio Sol SAS"],
    )

    assert "Juan Perez" not in res.sanitized_text
    assert "Comercio Sol SAS" not in res.sanitized_text
    assert "usuario.demo@example.local" not in res.sanitized_text
    assert "300 555 1234" not in res.sanitized_text
    assert "****4821" not in res.sanitized_text
    assert "10203040" not in res.sanitized_text

    # Guardrail passes on sanitized output.
    TextAnonymizer.assert_no_pii(res.sanitized_text)

    # Second call in same session reuses exact alias token for Juan Perez.
    res2 = anonymizer.anonymize("Reunion con Juan Perez", session_id="s-1")
    assert "Juan Perez" not in res2.sanitized_text
    assert res2.sanitized_text.split()[-1] in res.sanitized_text

    # Re-hydration restores local aliases accurately.
    restored = anonymizer.rehydrate(res.sanitized_text, session_id="s-1")
    assert restored == raw_text


def test_anonymize_payload_recursive_and_guardrail_rejects_raw_pii(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "local.db")
    anonymizer = TextAnonymizer(store=store, use_presidio=False)

    payload = {
        "question": "Revisa correo test@example.com",
        "items": ["Cuenta ****9012", {"nested": "tel 310-555-9988"}],
    }
    clean = anonymizer.anonymize_payload(payload, session_id="s-2")
    assert "test@example.com" not in str(clean)
    assert "****9012" not in str(clean)
    assert "310-555-9988" not in str(clean)
    TextAnonymizer.assert_no_pii(clean)

    with pytest.raises(PrivacyViolationError):
        TextAnonymizer.assert_no_pii({"leak": "user@example.com"})
