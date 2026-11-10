"""Deterministic Python PII anonymizer executed strictly before any Cloud LLM call."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from lup_ai.data.local_store import LocalStore

# Ordered patterns from most specific to most general to avoid partial overlaps.
_REGEX_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("URL", re.compile(r"https?://[^\s<>\"']+")),
    (
        "CUENTA",
        re.compile(
            r"(?:\*{2,4}\d{4}|\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b|"
            r"\bIBAN\s?[A-Z0-9]{10,30}\b)",
            re.IGNORECASE,
        ),
    ),
    ("ID_DOC", re.compile(r"\b(?:CC|NIT|CE|TI|DNI|PASAPORTE)[:\s#.-]*\d{5,15}\b", re.IGNORECASE)),
    (
        "TELEFONO",
        re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})(?!\d)"),
    ),
    (
        "PERSONA",
        re.compile(
            r"(\b(?:con|para|de|repartidor|cliente|usuario|dr\.|dra\.|sr\.|sra\.)\s+)"
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,2})"
        ),
    ),
)

_GUARDRAIL_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", _REGEX_RULES[0][1]),
    ("CUENTA", _REGEX_RULES[2][1]),
    ("ID_DOC", _REGEX_RULES[3][1]),
    ("TELEFONO", _REGEX_RULES[4][1]),
)


class PrivacyViolationError(ValueError):
    """Raised when unmasked PII is detected in an outbound payload."""


@dataclass(frozen=True)
class AnonymizationResult:
    """Sanitized text plus metadata of masked entities for the current session."""

    sanitized_text: str
    aliases_applied: dict[str, str] = field(default_factory=dict)
    engine_used: str = "deterministic_regex"


class TextAnonymizer:
    """Mask PII deterministically and persist reversible session aliases in LocalStore."""

    def __init__(self, store: LocalStore, use_presidio: bool = True) -> None:
        self._store = store
        self._presidio_analyzer = self._try_init_presidio() if use_presidio else None

    @staticmethod
    def _try_init_presidio() -> Any | None:
        try:
            from presidio_analyzer import AnalyzerEngine

            return AnalyzerEngine()
        except Exception:
            # Fallback to built-in deterministic regex rules when Presidio/spaCy models are absent.
            return None

    @property
    def engine_name(self) -> str:
        """Return active anonymization engine identifier."""
        if self._presidio_analyzer is not None:
            return "presidio_plus_regex"
        return "deterministic_regex"

    def _get_or_create_alias(
        self,
        session_id: str,
        entity_type: str,
        original_value: str,
        reverse_map: dict[str, str],
        counters: dict[str, int],
    ) -> str:
        existing = reverse_map.get(original_value)
        if existing is not None:
            return existing

        next_idx = counters.get(entity_type, 0) + 1
        counters[entity_type] = next_idx
        token = f"[{entity_type}_{next_idx}]"
        while token in reverse_map.values():
            next_idx += 1
            counters[entity_type] = next_idx
            token = f"[{entity_type}_{next_idx}]"

        reverse_map[original_value] = token
        self._store.store_alias(
            session_id=session_id,
            alias_token=token,
            entity_type=entity_type,
            original_value=original_value,
        )
        return token

    def _collect_presidio_spans(self, text: str) -> list[tuple[int, int, str]]:
        if self._presidio_analyzer is None or not text.strip():
            return []
        try:
            results = self._presidio_analyzer.analyze(text=text, language="en")
        except Exception:
            return []
        entity_map = {
            "PERSON": "PERSONA",
            "EMAIL_ADDRESS": "EMAIL",
            "PHONE_NUMBER": "TELEFONO",
            "CREDIT_CARD": "CUENTA",
            "IBAN_CODE": "CUENTA",
            "URL": "URL",
        }
        spans: list[tuple[int, int, str]] = []
        for item in results:
            mapped = entity_map.get(item.entity_type)
            if mapped and item.score >= 0.6:
                spans.append((item.start, item.end, mapped))
        return spans

    def anonymize(
        self,
        text: str,
        session_id: str,
        extra_sensitive_terms: Iterable[str] | None = None,
    ) -> AnonymizationResult:
        """Mask PII in text and store reversible aliases exclusively in LocalStore."""
        if not text:
            return AnonymizationResult(
                sanitized_text="", aliases_applied={}, engine_used=self.engine_name
            )

        session_aliases = self._store.get_session_aliases(session_id)
        reverse_map = {orig: tok for tok, orig in session_aliases.items()}
        counters: dict[str, int] = {}
        for tok in session_aliases:
            match = re.match(r"^\[([A-Z_]+)_(\d+)\]$", tok)
            if match:
                etype, num = match.group(1), int(match.group(2))
                counters[etype] = max(counters.get(etype, 0), num)

        sanitized = text
        applied: dict[str, str] = {}

        # Replace explicit caller-supplied sensitive strings first (longest first).
        if extra_sensitive_terms:
            sorted_terms = sorted(
                {t.strip() for t in extra_sensitive_terms if t and t.strip()},
                key=len,
                reverse=True,
            )
            for term in sorted_terms:
                if term in sanitized:
                    token = self._get_or_create_alias(
                        session_id, "ENTIDAD", term, reverse_map, counters
                    )
                    sanitized = sanitized.replace(term, token)
                    applied[token] = term

        # Apply optional Presidio spans before deterministic regex rules.
        presidio_spans = sorted(
            self._collect_presidio_spans(sanitized), key=lambda x: x[0], reverse=True
        )
        for start, end, etype in presidio_spans:
            fragment = sanitized[start:end]
            if re.match(r"^\[[A-Z_]+_\d+\]$", fragment):
                continue
            token = self._get_or_create_alias(session_id, etype, fragment, reverse_map, counters)
            sanitized = sanitized[:start] + token + sanitized[end:]
            applied[token] = fragment

        # Apply deterministic regex rules uniformly.
        for etype, pattern in _REGEX_RULES:

            def _replace(match_obj: re.Match[str], entity_label: str = etype) -> str:
                if match_obj.lastindex == 2:
                    prefix = match_obj.group(1)
                    original = match_obj.group(2)
                else:
                    prefix = ""
                    original = match_obj.group(0)
                token = self._get_or_create_alias(
                    session_id, entity_label, original, reverse_map, counters
                )
                applied[token] = original
                return f"{prefix}{token}"

            sanitized = pattern.sub(_replace, sanitized)

        return AnonymizationResult(
            sanitized_text=sanitized,
            aliases_applied=applied,
            engine_used=self.engine_name,
        )

    def anonymize_payload(
        self,
        payload: Any,
        session_id: str,
        extra_sensitive_terms: Iterable[str] | None = None,
    ) -> Any:
        """Recursively mask all string values inside a JSON-compatible payload."""
        if isinstance(payload, str):
            return self.anonymize(
                payload,
                session_id=session_id,
                extra_sensitive_terms=extra_sensitive_terms,
            ).sanitized_text
        if isinstance(payload, list):
            return [
                self.anonymize_payload(item, session_id, extra_sensitive_terms) for item in payload
            ]
        if isinstance(payload, dict):
            return {
                key: self.anonymize_payload(val, session_id, extra_sensitive_terms)
                for key, val in payload.items()
            }
        return payload

    def rehydrate(self, text: str, session_id: str) -> str:
        """Restore original local aliases into a response before returning to the user."""
        if not text:
            return text
        aliases = self._store.get_session_aliases(session_id)
        restored = text
        for token, original in aliases.items():
            if token in restored:
                restored = restored.replace(token, original)
        return restored

    @staticmethod
    def assert_no_pii(payload: Any) -> None:
        """Raise PrivacyViolationError if obvious raw PII patterns remain in outbound data."""
        serialized = payload if isinstance(payload, str) else repr(payload)
        for etype, pattern in _GUARDRAIL_PII_PATTERNS:
            if pattern.search(serialized):
                raise PrivacyViolationError(
                    f"Outbound masked_cloud payload contains unmasked {etype} entity."
                )
