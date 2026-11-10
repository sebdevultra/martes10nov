"""FastAPI server exposing LUP AI REST endpoints for CLI and future Streamlit UI."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Query

from lup_ai import __version__
from lup_ai.agent.orchestrator import LupOrchestrator, build_default_repository
from lup_ai.api.schemas import (
    AskRequest,
    AskResponse,
    CatalogQueryResponse,
    EvidenceSchema,
    GoalSchema,
    HealthResponse,
    PreferenceSchema,
)
from lup_ai.config.settings import Settings, get_settings
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import QueryCatalog
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.data.sql_guard import SQLGuardError
from lup_ai.llm.gemini_client import GeminiClient, LLMClient
from lup_ai.privacy.text_anonymizer import PrivacyViolationError


def create_app(
    settings: Settings | None = None,
    store: LocalStore | None = None,
    repo: AnalyticsRepository | None = None,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    """Create FastAPI instance with injectable dependencies for testing and deployment."""
    cfg = settings or get_settings()
    local_store = store or LocalStore(cfg.lup_sqlite_path)
    active_repo = repo or build_default_repository(cfg)
    active_llm = llm_client or GeminiClient(cfg)
    catalog = QueryCatalog(
        repo=active_repo,
        store=local_store,
        cache_ttl_seconds=cfg.lup_cache_ttl_seconds,
    )
    orchestrator = LupOrchestrator(
        settings=cfg,
        store=local_store,
        repo=active_repo,
        llm_client=active_llm,
    )

    api = FastAPI(
        title="LUP AI API",
        version=__version__,
        description="Personal Decision Intelligence Agent API (masked_cloud, Snowflake + Python).",
    )

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        sf_configured = SnowflakeAnalyticsRepository(cfg).is_configured()
        return HealthResponse(
            status="ok",
            version=__version__,
            privacy_mode=cfg.lup_privacy_mode,
            data_backend=active_repo.backend_name,
            gemini_configured=active_llm.is_configured(),
            snowflake_configured=sf_configured,
            sqlite_path=str(cfg.lup_sqlite_path),
        )

    @api.get("/v1/goals", response_model=list[GoalSchema])
    def list_goals() -> list[GoalSchema]:
        try:
            res = catalog.query_goals()
            if res.rows:
                return [GoalSchema(**row) for row in res.rows]
        except FileNotFoundError:
            pass
        return [GoalSchema(**row) for row in local_store.list_active_goals()]

    @api.get("/v1/preferences", response_model=list[PreferenceSchema])
    def list_preferences() -> list[PreferenceSchema]:
        return [PreferenceSchema(**row) for row in local_store.list_preferences()]

    @api.get("/v1/catalog/{domain}", response_model=CatalogQueryResponse)
    def query_catalog(
        domain: Literal["finance", "habits_sleep", "time_calendar", "cross_domain", "goals"],
        days: int = Query(default=30, ge=1, le=365),
        direction: Literal["income", "expense", "saving"] | None = None,
        category: str | None = None,
        discretionary_only: bool = False,
        domain_tag: str | None = None,
        block_type: str | None = None,
    ) -> CatalogQueryResponse:
        start_date, end_date = catalog.default_range(days=days)
        try:
            if domain == "finance":
                res = catalog.query_finance(
                    start_date=start_date,
                    end_date=end_date,
                    direction=direction,
                    category=category,
                    discretionary_only=discretionary_only,
                )
            elif domain == "habits_sleep":
                res = catalog.query_habits_sleep(start_date=start_date, end_date=end_date)
            elif domain == "time_calendar":
                res = catalog.query_time_calendar(
                    start_date=start_date,
                    end_date=end_date,
                    domain_tag=domain_tag,
                    block_type=block_type,
                )
            elif domain == "goals":
                res = catalog.query_goals()
            else:
                res = catalog.query_cross_domain(start_date=start_date, end_date=end_date)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return CatalogQueryResponse(
            domain=res.domain,
            source_view=res.source_view,
            backend=res.backend,
            cache_hit=res.cache_hit,
            start_date=res.start_date.isoformat() if res.start_date else None,
            end_date=res.end_date.isoformat() if res.end_date else None,
            aggregates=res.aggregates,
            rows=res.rows,
        )

    @api.post("/v1/ask", response_model=AskResponse)
    def ask(payload: AskRequest) -> AskResponse:
        try:
            result = orchestrator.ask(payload.question, session_id=payload.session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (SQLGuardError, PrivacyViolationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return AskResponse(
            session_id=result.session_id,
            answer=result.answer,
            privacy_mode="masked_cloud",
            anonymizer_engine=result.anonymizer_engine,
            data_backend=result.data_backend,
            model_used=result.model_used,
            domain=result.domain,
            key_observations=result.key_observations,
            evidence=EvidenceSchema(**asdict(result.evidence)),
            aggregates=result.aggregates,
            chart_series=result.chart_series,
        )

    return api


app = create_app()
