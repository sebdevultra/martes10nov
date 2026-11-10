"""FastAPI application and Pydantic contracts for LUP AI."""

from lup_ai.api.main import app, create_app
from lup_ai.api.schemas import (
    AskRequest,
    AskResponse,
    CatalogQueryResponse,
    EvidenceSchema,
    GoalSchema,
    HealthResponse,
    PreferenceSchema,
)

__all__ = [
    "AskRequest",
    "AskResponse",
    "CatalogQueryResponse",
    "EvidenceSchema",
    "GoalSchema",
    "HealthResponse",
    "PreferenceSchema",
    "app",
    "create_app",
]
