"""Decision Outcome Loop tracking recommendation acceptance and real-world impact."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from lup_ai.data.local_store import LocalStore
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.engines.recommendation_builder import RecommendationPayload

# Maps recommendation metric names onto VW_CROSS_DOMAIN_DAILY column names and improvement polarity.
_METRIC_EVAL_SPECS: dict[str, tuple[str, Literal["higher_better", "lower_better"]]] = {
    "discretionary_spend": ("discretionary_spend", "lower_better"),
    "next_day_discretionary_spend": ("discretionary_spend", "lower_better"),
    "total_spend": ("total_spend", "lower_better"),
    "meeting_hours": ("meeting_hours", "lower_better"),
    "sleep_hours": ("sleep_hours", "higher_better"),
    "sleep_hours_streak": ("sleep_hours", "higher_better"),
    "allocated_savings": ("allocated_savings", "higher_better"),
    "cumulative_savings": ("allocated_savings", "higher_better"),
    "net_cashflow": ("net_cashflow", "higher_better"),
    "unallocated_cashflow_30d": ("allocated_savings", "higher_better"),
    "deep_work_hours": ("deep_work_hours", "higher_better"),
    "weekly_deep_work_hours": ("deep_work_hours", "higher_better"),
    "recoverable_deep_work_hours_30d": ("deep_work_hours", "higher_better"),
    "habit_completion_rate": ("habit_completion_rate", "higher_better"),
    "energy_score": ("energy_score", "higher_better"),
}


class DecisionRecord(BaseModel):
    """Persisted recommendation and user action state."""

    decision_id: str
    created_at: str
    domain: str
    title: str
    prompt_summary: str
    model_used: str
    privacy_mode: str
    source_type: str
    metric_name: str
    baseline_value: float | None = None
    user_action: str
    user_feedback: str | None = None
    actioned_at: str | None = None
    evaluate_after_days: int = 7
    recommendation: dict[str, Any] = Field(default_factory=dict)


class DecisionOutcomeRecord(BaseModel):
    """Measured impact of an accepted decision after its evaluation window."""

    outcome_id: str
    decision_id: str
    evaluated_at: str
    baseline_value: float | None = None
    observed_value: float | None = None
    delta_impact: float
    outcome_status: Literal["improved", "unchanged", "regressed"]
    user_feedback: str | None = None


class AdherenceSummary(BaseModel):
    """Compact summary of historical decision acceptance and real improvement rates."""

    total_decisions: int
    pending_count: int
    accepted_count: int
    dismissed_count: int
    completed_count: int
    evaluated_outcomes: int
    improved_count: int
    acceptance_rate_pct: float
    improvement_rate_pct: float


def _parse_iso_date(raw_ts: str | None, fallback: date) -> date:
    if not raw_ts:
        return fallback
    try:
        return datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).date()
    except ValueError:
        return fallback


class DecisionTracker:
    """Persist recommendations, capture user feedback, and evaluate real outcomes."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    def persist_recommendations(
        self,
        recommendations: list[RecommendationPayload],
        source_type: Literal["interactive", "proactive_scan"] = "interactive",
        model_used: str = "deterministic_catalog",
        prompt_summary: str = "",
        evaluate_after_days: int = 7,
    ) -> list[str]:
        """Store recommendations idempotently in local decisions_log."""
        ids: list[str] = []
        for rec in recommendations:
            primary_ev = rec.evidence[0]
            self._store.record_decision(
                {
                    "decision_id": rec.recommendation_id,
                    "domain": rec.domain,
                    "title": rec.title,
                    "prompt_summary": prompt_summary[:500],
                    "recommendation_json": rec.model_dump(),
                    "evidence_json": [ev.model_dump() for ev in rec.evidence],
                    "model_used": model_used,
                    "privacy_mode": "masked_cloud",
                    "source_type": source_type,
                    "metric_name": primary_ev.metric_name,
                    "baseline_value": primary_ev.baseline_value,
                    "user_action": "pending",
                    "evaluate_after_days": evaluate_after_days,
                }
            )
            ids.append(rec.recommendation_id)
        return ids

    @staticmethod
    def _row_to_decision(row: dict[str, Any]) -> DecisionRecord:
        try:
            rec_obj = json.loads(row["recommendation_json"])
        except Exception:
            rec_obj = {}
        return DecisionRecord(
            decision_id=row["decision_id"],
            created_at=row["created_at"],
            domain=row["domain"],
            title=row["title"],
            prompt_summary=row.get("prompt_summary", ""),
            model_used=row["model_used"],
            privacy_mode=row.get("privacy_mode", "masked_cloud"),
            source_type=row.get("source_type", "interactive"),
            metric_name=row.get("metric_name", ""),
            baseline_value=row.get("baseline_value"),
            user_action=row["user_action"],
            user_feedback=row.get("user_feedback"),
            actioned_at=row.get("actioned_at"),
            evaluate_after_days=int(row.get("evaluate_after_days", 7)),
            recommendation=rec_obj,
        )

    def list_decisions(
        self,
        user_action: str | None = None,
        limit: int = 50,
    ) -> list[DecisionRecord]:
        """List decision inbox or history."""
        rows = self._store.list_decisions(user_action=user_action, limit=limit)
        return [self._row_to_decision(r) for r in rows]

    def record_feedback(
        self,
        decision_id: str,
        user_action: Literal["accepted", "dismissed", "completed"],
        user_feedback: str | None = None,
        actioned_at: str | None = None,
        evaluate_after_days: int | None = None,
    ) -> DecisionRecord:
        """Record user acceptance or dismissal for a stored recommendation."""
        existing = self._store.get_decision(decision_id)
        if existing is None:
            raise KeyError(f"Decision '{decision_id}' not found.")
        updated = self._store.update_decision_action(
            decision_id=decision_id,
            user_action=user_action,
            user_feedback=user_feedback,
            actioned_at=actioned_at,
            evaluate_after_days=evaluate_after_days,
        )
        assert updated is not None
        return self._row_to_decision(updated)

    def evaluate_pending_outcomes(
        self,
        repo: AnalyticsRepository,
        anchor_date: date | None = None,
        force: bool = False,
    ) -> list[DecisionOutcomeRecord]:
        """Evaluate accepted decisions whose observation window has elapsed."""
        ref_date = anchor_date or date(2026, 11, 10)
        accepted = self._store.list_decisions(user_action="accepted", limit=100)
        outcomes: list[DecisionOutcomeRecord] = []

        for dec in accepted:
            action_date = _parse_iso_date(dec.get("actioned_at") or dec.get("created_at"), ref_date)
            wait_days = int(dec.get("evaluate_after_days", 7))
            if not force and ref_date < action_date + timedelta(days=wait_days):
                continue

            metric_name = dec.get("metric_name", "")
            spec = _METRIC_EVAL_SPECS.get(metric_name)
            if spec is None:
                continue

            col_name, polarity = spec
            window_end = min(ref_date, action_date + timedelta(days=max(wait_days, 7)))
            rows = repo.fetch_cross_domain_daily(start_date=action_date, end_date=window_end)
            values = [float(row[col_name]) for row in rows if row.get(col_name) is not None]
            if not values:
                continue

            observed = round(sum(values) / len(values), 4)
            baseline_raw = dec.get("baseline_value")
            if baseline_raw is None:
                continue
            baseline = float(baseline_raw)

            # For cumulative/30d opportunity metrics, compare against historical daily baseline.
            if metric_name in {"unallocated_cashflow_30d", "recoverable_deep_work_hours_30d"}:
                hist_start = action_date - timedelta(days=30)
                hist_rows = repo.fetch_cross_domain_daily(
                    start_date=hist_start,
                    end_date=action_date - timedelta(days=1),
                )
                hist_vals = [float(r[col_name]) for r in hist_rows if r.get(col_name) is not None]
                if hist_vals:
                    baseline = round(sum(hist_vals) / len(hist_vals), 4)

            delta = round(observed - baseline, 4)
            tolerance = max(0.15, abs(baseline) * 0.03)

            if abs(delta) <= tolerance:
                status: Literal["improved", "unchanged", "regressed"] = "unchanged"
            elif (polarity == "higher_better" and delta > 0) or (
                polarity == "lower_better" and delta < 0
            ):
                status = "improved"
            else:
                status = "regressed"

            outcome_id = f"out_{dec['decision_id']}"
            outcome_payload = {
                "outcome_id": outcome_id,
                "decision_id": dec["decision_id"],
                "evaluated_at": ref_date.isoformat(),
                "baseline_value": round(baseline, 4),
                "observed_value": observed,
                "delta_impact": delta,
                "outcome_status": status,
                "user_feedback": dec.get("user_feedback"),
            }
            self._store.record_outcome(outcome_payload)
            self._store.update_decision_action(dec["decision_id"], user_action="completed")
            outcomes.append(DecisionOutcomeRecord(**outcome_payload))

        return outcomes

    def list_outcomes(self, decision_id: str | None = None) -> list[DecisionOutcomeRecord]:
        """Return persisted decision outcomes."""
        return [DecisionOutcomeRecord(**row) for row in self._store.list_outcomes(decision_id)]

    def adherence_summary(self) -> AdherenceSummary:
        """Compute historical acceptance and outcome improvement rates."""
        all_decisions = self._store.list_decisions(limit=500)
        pending = sum(1 for d in all_decisions if d["user_action"] == "pending")
        accepted = sum(1 for d in all_decisions if d["user_action"] == "accepted")
        dismissed = sum(1 for d in all_decisions if d["user_action"] == "dismissed")
        completed = sum(1 for d in all_decisions if d["user_action"] == "completed")

        outcomes = self._store.list_outcomes()
        improved = sum(1 for o in outcomes if o["outcome_status"] == "improved")

        actioned_total = accepted + dismissed + completed
        acceptance_rate = (
            round(((accepted + completed) / actioned_total) * 100.0, 1)
            if actioned_total > 0
            else 0.0
        )
        improvement_rate = round((improved / len(outcomes)) * 100.0, 1) if outcomes else 0.0

        return AdherenceSummary(
            total_decisions=len(all_decisions),
            pending_count=pending,
            accepted_count=accepted,
            dismissed_count=dismissed,
            completed_count=completed,
            evaluated_outcomes=len(outcomes),
            improved_count=improved,
            acceptance_rate_pct=acceptance_rate,
            improvement_rate_pct=improvement_rate,
        )
