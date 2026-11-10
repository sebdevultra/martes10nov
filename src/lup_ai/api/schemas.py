"""Pydantic v2 HTTP schemas shared by FastAPI, CLI, and future Streamlit client."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    key_observations: list[str]
    evidence: EvidenceSchema
    aggregates: dict[str, Any]
    chart_series: list[dict[str, Any]]


class GoalSchema(BaseModel):
    """Active user goal representation."""

    goal_id: str
    domain: str
    title: str
    metric_name: str
    target_value: float
    unit: str
    comparison_operator: str
    deadline: str | None = None
    priority: int
    status: str
    latest_week: str | None = None
    latest_observed_value: float | None = None


class PreferenceSchema(BaseModel):
    """User preference or hard constraint representation."""

    pref_id: str
    domain: str
    key_name: str
    value_json: str
    is_hard_constraint: bool


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
