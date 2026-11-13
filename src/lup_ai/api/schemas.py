"""Pydantic v2 HTTP schemas shared by FastAPI, CLI, and future Streamlit client."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from lup_ai.connectors.simulated_sources import SimulatedSyncReport
from lup_ai.data.batch_ingestor import CaptureResult, FlushResult
from lup_ai.engines.anomaly_detector import AnomalyScanResult
from lup_ai.engines.recommendation_builder import RecommendationPayload
from lup_ai.engines.simulation_engine import SimulationResult, SimulationScenario
from lup_ai.engines.trend_analyzer import TrendAnalysisResult
from lup_ai.jobs.proactive_scan import ProactiveScanReport
from lup_ai.memory.decision_tracker import (
    AdherenceSummary,
    DecisionOutcomeRecord,
    DecisionRecord,
)


class HealthResponse(BaseModel):
    """Service readiness and active privacy/backend metadata."""

    status: Literal["ok"] = "ok"
    version: str
    privacy_mode: Literal["masked_cloud"]
    data_backend: str
    gemini_configured: bool
    snowflake_configured: bool
    sqlite_path: str


class AskRequest(BaseModel):
    """Natural-language query request."""

    question: str = Field(..., min_length=2, max_length=2000)
    session_id: str | None = Field(default=None, max_length=128)


class EvidenceSchema(BaseModel):
    """Data provenance attached to every agent response."""

    source_view: str
    backend: str
    cache_hit: bool
    start_date: str | None = None
    end_date: str | None = None
    row_count: int


class AskResponse(BaseModel):
    """Structured response ready for CLI rendering and future Streamlit visualizations."""

    session_id: str
    answer: str
    privacy_mode: Literal["masked_cloud"]
    anonymizer_engine: str
    data_backend: str
    model_used: str
    domain: str
    analysis_type: str = "summary"
    key_observations: list[str]
    evidence: EvidenceSchema
    aggregates: dict[str, Any]
    chart_series: list[dict[str, Any]]
    recommendations: list[RecommendationPayload] = Field(default_factory=list)
    trends: TrendAnalysisResult | None = None
    anomaly_scan: AnomalyScanResult | None = None
    simulation: SimulationResult | None = None


class GoalSchema(BaseModel):
    """Active user goal representation."""

    goal_id: str
    domain: str
    title: str
    metric_name: str
    target_value: float
    unit: str
    comparison_operator: str = ">="
    deadline: str | None = None
    priority: int = 1
    status: str = "active"
    latest_week: str | None = None
    latest_observed_value: float | None = None


class GoalCreate(BaseModel):
    """Payload to create a new goal (omits goal_id, created_at, updated_at)."""

    domain: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=160)
    metric_name: str = Field(..., min_length=1, max_length=64)
    target_value: float
    unit: str = Field(..., min_length=1, max_length=32)
    comparison_operator: str = Field(default=">=", max_length=8)
    deadline: str | None = None
    priority: int = Field(default=1, ge=1, le=10)
    status: str = Field(default="active", max_length=16)


class GoalUpdate(BaseModel):
    """Payload to update an existing goal (all fields optional)."""

    domain: str | None = None
    title: str | None = None
    metric_name: str | None = None
    target_value: float | None = None
    unit: str | None = None
    comparison_operator: str | None = None
    deadline: str | None = None
    priority: int | None = None
    status: str | None = None


class PreferenceSchema(BaseModel):
    """User preference or hard constraint representation."""

    pref_id: str
    domain: str
    key_name: str
    value_json: str
    is_hard_constraint: bool = False


class PreferenceCreate(BaseModel):
    """Payload to create a new preference or hard constraint."""

    pref_id: str | None = None
    domain: str = Field(..., min_length=1, max_length=32)
    key_name: str = Field(..., min_length=1, max_length=64)
    value_json: str = Field(..., min_length=1)
    is_hard_constraint: bool = False


class PreferenceUpdate(BaseModel):
    """Payload to update an existing preference (all fields optional)."""

    domain: str | None = None
    key_name: str | None = None
    value_json: str | None = None
    is_hard_constraint: bool | None = None


class CatalogQueryResponse(BaseModel):
    """Direct parameterized query output across SEMANTIC views."""

    domain: str
    source_view: str
    backend: str
    cache_hit: bool
    start_date: str | None = None
    end_date: str | None = None
    aggregates: dict[str, Any]
    rows: list[dict[str, Any]]


class TrendsResponse(BaseModel):
    """Deterministic behavior and correlation report."""

    evidence: EvidenceSchema
    analysis: TrendAnalysisResult


class AnomaliesResponse(BaseModel):
    """Detected anomalies, opportunities, and justified recommendations."""

    evidence: EvidenceSchema
    scan: AnomalyScanResult
    recommendations: list[RecommendationPayload]


class SimulateRequest(BaseModel):
    """What-If simulation request payload."""

    scenario: SimulationScenario = Field(default_factory=SimulationScenario)
    baseline_days: int = Field(default=180, ge=14, le=365)


class SimulateResponse(BaseModel):
    """What-If deterministic trajectories and justified recommendations."""

    evidence: EvidenceSchema
    simulation: SimulationResult
    recommendations: list[RecommendationPayload]


class DecisionFeedbackRequest(BaseModel):
    """User feedback payload for a stored decision."""

    user_action: Literal["accepted", "dismissed", "completed"]
    user_feedback: str | None = Field(default=None, max_length=500)
    evaluate_after_days: int | None = Field(default=None, ge=1, le=90)


class OutcomesResponse(BaseModel):
    """Historical decision adherence metrics and evaluated outcomes."""

    adherence_summary: AdherenceSummary
    outcomes: list[DecisionOutcomeRecord]


class ProactiveScanRequest(BaseModel):
    """Optional parameters for triggering a proactive scan."""

    days: int = Field(default=90, ge=14, le=365)
    evaluate_outcomes_force: bool = False


class CaptureEventRequest(BaseModel):
    """Unified incremental event capture request."""

    domain: Literal["finance", "habits_sleep", "time_calendar"]
    payload: dict[str, Any]


class PendingIngestResponse(BaseModel):
    """Summary of buffered events awaiting batch flush."""

    pending_count: int
    entries: list[dict[str, Any]]


class SimulatedSyncRequest(BaseModel):
    """Parameters for pulling simulated external feeds into BatchIngestor."""

    days: int = Field(default=3, ge=1, le=30)
    seed: int = 101
    auto_flush: bool = True
    sources: list[Literal["finance", "habits_sleep", "time_calendar"]] | None = None


class ManualIngestResponse(BaseModel):
    """Summary of events captured through real source connectors."""

    status: str = "ok"
    domain: str
    connector: str
    events_captured: int
    entry_ids: list[str] = Field(default_factory=list)
    flushed: bool = False
    flush_result: FlushResult | None = None


from lup_ai.jobs.scheduler import SchedulerStatus  # noqa: E402

__all__ = [
    "AnomaliesResponse",
    "AskRequest",
    "AskResponse",
    "CaptureEventRequest",
    "CaptureResult",
    "CatalogQueryResponse",
    "DecisionFeedbackRequest",
    "DecisionRecord",
    "EvidenceSchema",
    "FlushResult",
    "GoalCreate",
    "GoalSchema",
    "GoalUpdate",
    "HealthResponse",
    "ManualIngestResponse",
    "OutcomesResponse",
    "PendingIngestResponse",
    "PreferenceCreate",
    "PreferenceSchema",
    "PreferenceUpdate",
    "ProactiveScanReport",
    "ProactiveScanRequest",
    "SchedulerStatus",
    "SimulateRequest",
    "SimulateResponse",
    "SimulatedSyncReport",
    "SimulatedSyncRequest",
    "TrendsResponse",
]
