"""FastAPI server exposing LUP AI REST endpoints for CLI and future Streamlit UI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import ValidationError

from lup_ai import __version__
from lup_ai.agent.orchestrator import LupOrchestrator, build_default_repository
from lup_ai.api.schemas import (
    AnomaliesResponse,
    AskRequest,
    AskResponse,
    CaptureEventRequest,
    CaptureResult,
    CatalogQueryResponse,
    DecisionFeedbackRequest,
    DecisionRecord,
    EvidenceSchema,
    FlushResult,
    GoalSchema,
    HealthResponse,
    OutcomesResponse,
    PendingIngestResponse,
    PreferenceSchema,
    ProactiveScanReport,
    ProactiveScanRequest,
    SchedulerStatus,
    SimulatedSyncReport,
    SimulatedSyncRequest,
    SimulateRequest,
    SimulateResponse,
    TrendsResponse,
)
from lup_ai.config.settings import Settings, get_settings
from lup_ai.connectors.simulated_sources import SimulatedConnectorHub
from lup_ai.data.batch_ingestor import (
    BatchIngestor,
    FinanceEventInput,
    HabitsSleepEventInput,
    TimeBlockEventInput,
)
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import QueryCatalog
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.data.sql_guard import SQLGuardError
from lup_ai.jobs.proactive_scan import ProactiveScanner
from lup_ai.jobs.scheduler import LupScheduler
from lup_ai.llm.gemini_client import GeminiClient, LLMClient
from lup_ai.memory.decision_tracker import DecisionTracker
from lup_ai.privacy.text_anonymizer import PrivacyViolationError


def create_app(
    settings: Settings | None = None,
    store: LocalStore | None = None,
    repo: AnalyticsRepository | None = None,
    llm_client: LLMClient | None = None,
    enable_scheduler: bool | None = None,
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
    decision_tracker = DecisionTracker(local_store)
    scanner = ProactiveScanner(store=local_store, repo=active_repo)
    ingestor = BatchIngestor(
        settings=cfg,
        store=local_store,
        local_csv_repo=(
            active_repo if isinstance(active_repo, LocalCsvAnalyticsRepository) else None
        ),
    )
    connector_hub = SimulatedConnectorHub(ingestor=ingestor)
    scheduler = LupScheduler(
        settings=cfg,
        store=local_store,
        repo=active_repo,
        ingestor=ingestor,
        scanner=scanner,
    )
    should_start_scheduler = (
        cfg.lup_scheduler_enabled if enable_scheduler is None else enable_scheduler
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if should_start_scheduler:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    api = FastAPI(
        title="LUP AI API",
        version=__version__,
        description="Personal Decision Intelligence Agent API (masked_cloud, Snowflake + Python).",
        lifespan=lifespan,
    )
    api.state.scheduler = scheduler

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

    @api.get("/v1/trends", response_model=TrendsResponse)
    def get_trends(days: int = Query(default=90, ge=14, le=365)) -> TrendsResponse:
        try:
            analysis, evidence = orchestrator.analyze_trends(days=days)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return TrendsResponse(
            evidence=EvidenceSchema(**asdict(evidence)),
            analysis=analysis,
        )

    @api.get("/v1/anomalies", response_model=AnomaliesResponse)
    def get_anomalies(days: int = Query(default=90, ge=14, le=365)) -> AnomaliesResponse:
        try:
            scan, recommendations, evidence = orchestrator.scan_anomalies(days=days)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return AnomaliesResponse(
            evidence=EvidenceSchema(**asdict(evidence)),
            scan=scan,
            recommendations=recommendations,
        )

    @api.post("/v1/simulate", response_model=SimulateResponse)
    def simulate(payload: SimulateRequest) -> SimulateResponse:
        try:
            sim_result, recommendations, evidence = orchestrator.simulate(
                scenario=payload.scenario,
                baseline_days=payload.baseline_days,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return SimulateResponse(
            evidence=EvidenceSchema(**asdict(evidence)),
            simulation=sim_result,
            recommendations=recommendations,
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
            analysis_type=result.analysis_type,
            key_observations=result.key_observations,
            evidence=EvidenceSchema(**asdict(result.evidence)),
            aggregates=result.aggregates,
            chart_series=result.chart_series,
            recommendations=result.recommendations,
            trends=result.trends,
            anomaly_scan=result.anomaly_scan,
            simulation=result.simulation,
        )

    @api.get("/v1/decisions/outcomes", response_model=OutcomesResponse)
    def list_decision_outcomes(decision_id: str | None = None) -> OutcomesResponse:
        return OutcomesResponse(
            adherence_summary=decision_tracker.adherence_summary(),
            outcomes=decision_tracker.list_outcomes(decision_id=decision_id),
        )

    @api.get("/v1/decisions", response_model=list[DecisionRecord])
    def list_decisions(
        status: Literal["pending", "accepted", "dismissed", "completed"] | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[DecisionRecord]:
        return decision_tracker.list_decisions(user_action=status, limit=limit)

    @api.post("/v1/decisions/{decision_id}/feedback", response_model=DecisionRecord)
    def record_decision_feedback(
        decision_id: str,
        payload: DecisionFeedbackRequest,
    ) -> DecisionRecord:
        try:
            return decision_tracker.record_feedback(
                decision_id=decision_id,
                user_action=payload.user_action,
                user_feedback=payload.user_feedback,
                evaluate_after_days=payload.evaluate_after_days,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/v1/jobs/proactive-scan", response_model=ProactiveScanReport)
    def run_proactive_scan(
        payload: ProactiveScanRequest | None = None,
    ) -> ProactiveScanReport:
        req = payload or ProactiveScanRequest()
        try:
            return scanner.run(
                lookback_days=req.days,
                evaluate_outcomes_force=req.evaluate_outcomes_force,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @api.get("/v1/jobs/scheduler", response_model=SchedulerStatus)
    def get_scheduler_status() -> SchedulerStatus:
        return scheduler.status()

    @api.get("/v1/ingest/pending", response_model=PendingIngestResponse)
    def list_pending_ingest() -> PendingIngestResponse:
        entries = local_store.list_pending_ingest_events()
        return PendingIngestResponse(pending_count=len(entries), entries=entries)

    @api.post("/v1/ingest/capture", response_model=CaptureResult)
    def capture_event(payload: CaptureEventRequest) -> CaptureResult:
        try:
            if payload.domain == "finance":
                return ingestor.capture_finance(FinanceEventInput.model_validate(payload.payload))
            if payload.domain == "habits_sleep":
                return ingestor.capture_habits_sleep(
                    HabitsSleepEventInput.model_validate(payload.payload)
                )
            return ingestor.capture_time_block(TimeBlockEventInput.model_validate(payload.payload))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    @api.post("/v1/ingest/flush", response_model=FlushResult)
    def flush_ingest_batch() -> FlushResult:
        return ingestor.flush_batch()

    @api.post("/v1/connectors/simulate-sync", response_model=SimulatedSyncReport)
    def sync_simulated_sources(
        payload: SimulatedSyncRequest | None = None,
    ) -> SimulatedSyncReport:
        req = payload or SimulatedSyncRequest()
        return connector_hub.sync_all(
            days=req.days,
            seed=req.seed,
            auto_flush=req.auto_flush,
            sources=req.sources,
        )

    return api


app = create_app()
