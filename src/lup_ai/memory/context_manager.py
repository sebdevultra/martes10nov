"""Compact sanitized context builder over LocalStore goals, preferences, and recent turns."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from lup_ai.data.local_store import LocalStore
from lup_ai.memory.decision_tracker import DecisionTracker
from lup_ai.privacy.text_anonymizer import TextAnonymizer


@dataclass(frozen=True)
class SanitizedContextPacket:
    """Compact user context safe for outbound masked_cloud LLM prompts."""

    session_id: str
    active_goals: list[dict[str, Any]]
    hard_constraints: list[dict[str, Any]]
    preferences: list[dict[str, Any]]
    recent_turns: list[dict[str, str]]
    decision_adherence: dict[str, Any]


class ContextManager:
    """Assemble compact goal/preference/history context and persist chat turns."""

    def __init__(
        self,
        store: LocalStore,
        anonymizer: TextAnonymizer,
        max_goals: int = 5,
        max_turns: int = 4,
    ) -> None:
        self._store = store
        self._anonymizer = anonymizer
        self._tracker = DecisionTracker(store)
        self._max_goals = max_goals
        self._max_turns = max_turns

    def build_sanitized_packet(self, session_id: str) -> SanitizedContextPacket:
        """Build a token-bounded, PII-masked context packet for the session."""
        self._store.ensure_session(session_id)

        raw_goals = self._store.list_active_goals()[: self._max_goals]
        compact_goals = [
            {
                "goal_id": g["goal_id"],
                "domain": g["domain"],
                "title": g["title"],
                "metric_name": g["metric_name"],
                "target": f"{g['comparison_operator']} {g['target_value']} {g['unit']}",
                "deadline": g.get("deadline"),
            }
            for g in raw_goals
        ]

        all_prefs = self._store.list_preferences()
        hard_constraints: list[dict[str, Any]] = []
        soft_prefs: list[dict[str, Any]] = []
        for pref in all_prefs:
            try:
                parsed_val = json.loads(pref["value_json"])
            except Exception:
                parsed_val = pref["value_json"]
            compact_pref = {
                "domain": pref["domain"],
                "key": pref["key_name"],
                "value": parsed_val,
            }
            if pref["is_hard_constraint"]:
                hard_constraints.append(compact_pref)
            else:
                soft_prefs.append(compact_pref)

        recent = self._store.get_recent_messages(session_id, limit=self._max_turns)
        compact_turns = [
            {
                "role": m["role"],
                "content": m["content"][:300],
            }
            for m in recent
        ]

        adherence = self._tracker.adherence_summary().model_dump()

        return SanitizedContextPacket(
            session_id=session_id,
            active_goals=self._anonymizer.anonymize_payload(compact_goals, session_id),
            hard_constraints=self._anonymizer.anonymize_payload(hard_constraints, session_id),
            preferences=self._anonymizer.anonymize_payload(soft_prefs, session_id),
            recent_turns=self._anonymizer.anonymize_payload(compact_turns, session_id),
            decision_adherence=adherence,
        )

    def record_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        model_used: str | None = None,
    ) -> None:
        """Persist a chat message in LocalStore."""
        self._store.append_message(
            message_id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            model_used=model_used,
        )
