"""Central orchestrator wiring Python anonymizer, QueryCatalog, SQL Guard, and Gemini 3.8."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from lup_ai.config.settings import Settings
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.data.query_catalog import CatalogResult, QueryCatalog
from lup_ai.data.repository import AnalyticsRepository
from lup_ai.data.snowflake_client import SnowflakeAnalyticsRepository
from lup_ai.data.sql_guard import SQLGuardError
from lup_ai.llm.gemini_client import GeminiClient, IntentPlan, LLMClient, SynthesizedAnswer
from lup_ai.memory.context_manager import ContextManager, SanitizedContextPacket
from lup_ai.privacy.text_anonymizer import TextAnonymizer

_PLANNER_SYSTEM_PROMPT = (
    "You are LUP AI query planner. Select the domain and either parameterized catalog filters "
    "or a read-only Snowflake SELECT query over LUP_AI_DB.SEMANTIC views: "
    "VW_FINANCE_SUMMARY, VW_HABITS_SLEEP_SUMMARY, VW_TIME_CALENDAR_SUMMARY, "
    "VW_CROSS_DOMAIN_DAILY, VW_GOAL_PROGRESS. Never invent metrics or reference RAW/CORE tables."
)

_SYNTHESIS_SYSTEM_PROMPT = (
    "You are LUP AI personal decision analyst. Explain ONLY the numeric metrics and evidence "
    "provided in the JSON payload. Never invent numbers, dates, or tables. Distinguish correlation "
    "from causation and align observations with active_goals and hard_constraints."
)


@dataclass(frozen=True)
class EvidenceItem:
    """Traceability record linking answers to queried semantic views and date windows."""

    source_view: str
    backend: str
    cache_hit: bool
    start_date: str | None
    end_date: str | None
    row_count: int


@dataclass(frozen=True)
class AskResult:
    """Structured orchestration output consumable by CLI, FastAPI, and future Streamlit UI."""

    session_id: str
    answer: str
    privacy_mode: str
    anonymizer_engine: str
    data_backend: str
    model_used: str
    domain: str
    key_observations: list[str]
    evidence: EvidenceItem
    aggregates: dict[str, Any]
    chart_series: list[dict[str, Any]] = field(default_factory=list)


def build_default_repository(settings: Settings) -> AnalyticsRepository:
    """Prefer Snowflake when configured; otherwise use mirrored local synthetic CSVs."""
    sf_repo = SnowflakeAnalyticsRepository(settings)
    if sf_repo.is_configured():
        return sf_repo
    return LocalCsvAnalyticsRepository()


def _extract_days_from_text(text: str, default_days: int = 30) -> int:
    lowered = text.lower()
    if any(tok in lowered for tok in ("semana", "semanal", "week", "7 dias", "7 días")):
        return 7
    if any(tok in lowered for tok in ("trimestre", "90 dias", "90 días", "3 meses")):
        return 90
    if any(tok in lowered for tok in ("6 meses", "180 dias", "180 días", "semestre")):
        return 180
    match = re.search(r"\b(\d{1,3})\s*(?:dias|días|days)\b", lowered)
    if match:
        return max(1, min(365, int(match.group(1))))
    return default_days


def _deterministic_fast_intent(sanitized_question: str) -> IntentPlan | None:
    """Resolve clear single-domain or goal queries without burning cloud tokens."""
    text = sanitized_question.lower()
    days = _extract_days_from_text(text, default_days=30)

    has_finance = any(
        w in text
        for w in ("gasto", "gastos", "finanza", "ahorro", "ingreso", "dinero", "spend", "budget")
    )
    has_sleep = any(
        w in text
        for w in (
            "sueño",
            "sueno",
            "dorm",
            "habito",
            "hábito",
            "energia",
            "energía",
            "ejercicio",
            "sleep",
        )
    )
    has_time = any(
        w in text
        for w in (
            "tiempo",
            "calendario",
            "reunion",
            "reunión",
            "foco",
            "deep work",
            "agenda",
            "horas",
        )
    )
    has_goals = any(w in text for w in ("meta", "metas", "objetivo", "objetivos", "goal", "goals"))

    domain_hits = sum([has_finance, has_sleep, has_time])
    if domain_hits >= 2 or any(
        w in text for w in ("cruzad", "correlac", "general", "resumen completo")
    ):
        return IntentPlan(
            domain="cross_domain", mode="catalog", days=days, reasoning="deterministic_cross_domain"
        )
    if has_goals and domain_hits == 0:
        return IntentPlan(
            domain="goals", mode="catalog", days=days, reasoning="deterministic_goals"
        )
    if has_finance:
        discretionary = any(w in text for w in ("discrecional", "ocio", "domicilio", "delivery"))
        return IntentPlan(
            domain="finance",
            mode="catalog",
            days=days,
            discretionary_only=discretionary,
            reasoning="deterministic_finance",
        )
    if has_sleep:
        return IntentPlan(
            domain="habits_sleep", mode="catalog", days=days, reasoning="deterministic_habits_sleep"
        )
    if has_time:
        return IntentPlan(
            domain="time_calendar",
            mode="catalog",
            days=days,
            reasoning="deterministic_time_calendar",
        )
    return None


def _format_deterministic_summary(plan: IntentPlan, result: CatalogResult) -> SynthesizedAnswer:
    agg = result.aggregates
    if result.domain == "finance":
        answer = (
            f"Resumen financiero ({result.start_date} a {result.end_date}): "
            f"ingresos={agg.get('total_income', 0)}, gastos={agg.get('total_spend', 0)}, "
            f"gasto discrecional={agg.get('discretionary_spend', 0)}, "
            f"ahorro asignado={agg.get('allocated_savings', 0)}, "
            f"flujo neto={agg.get('net_cashflow', 0)}."
        )
        top_cats = list((agg.get("spend_by_category") or {}).items())[:3]
        obs = [f"Categoria {cat}: {val}" for cat, val in top_cats]
        return SynthesizedAnswer(answer=answer, key_observations=obs)

    if result.domain == "habits_sleep":
        adherence_pct = round(float(agg.get("avg_habit_completion_rate", 0)) * 100, 1)
        answer = (
            f"Resumen de habitos y sueno ({result.start_date} a {result.end_date}): "
            f"promedio sueno={agg.get('avg_sleep_hours', 0)}h, "
            f"calidad={agg.get('avg_sleep_quality_score', 0)}, "
            f"adherencia habitos={adherence_pct}%, "
            f"ejercicio total={agg.get('total_exercise_minutes', 0)} min."
        )
        return SynthesizedAnswer(
            answer=answer,
            key_observations=[f"Energia promedio: {agg.get('avg_energy_score', 0)}/10"],
        )

    if result.domain == "time_calendar":
        answer = (
            f"Resumen de tiempo y calendario ({result.start_date} a {result.end_date}): "
            f"horas completadas={agg.get('total_completed_hours', 0)}h."
        )
        blocks = list((agg.get("hours_by_block_type") or {}).items())
        obs = [f"Bloque {btype}: {hrs}h" for btype, hrs in blocks]
        return SynthesizedAnswer(answer=answer, key_observations=obs)

    if result.domain == "goals":
        lines = [
            (
                f"{row['title']}: observado={row.get('latest_observed_value')} "
                f"vs meta {row['comparison_operator']} {row['target_value']} {row['unit']}"
            )
            for row in result.rows
        ]
        answer = (
            f"Objetivos activos ({len(result.rows)}): " + "; ".join(lines)
            if lines
            else "No hay objetivos activos registrados."
        )
        return SynthesizedAnswer(answer=answer, key_observations=lines)

    answer = (
        f"Resumen multi-dominio ({result.start_date} a {result.end_date}): "
        f"gasto={agg.get('total_spend', 0)}, ahorro={agg.get('allocated_savings', 0)}, "
        f"sueno promedio={agg.get('avg_sleep_hours', 0)}h, "
        f"deep work={agg.get('total_deep_work_hours', 0)}h, "
        f"reuniones={agg.get('total_meeting_hours', 0)}h."
    )
    return SynthesizedAnswer(
        answer=answer, key_observations=[f"Dias analizados: {agg.get('row_count', 0)}"]
    )


class LupOrchestrator:
    """Coordinate masked_cloud privacy boundary, deterministic queries, and Gemini synthesis."""

    def __init__(
        self,
        settings: Settings,
        store: LocalStore,
        repo: AnalyticsRepository | None = None,
        llm_client: LLMClient | None = None,
        anchor_date: date | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._repo = repo or build_default_repository(settings)
        self._catalog = QueryCatalog(
            repo=self._repo,
            store=store,
            cache_ttl_seconds=settings.lup_cache_ttl_seconds,
        )
        self._anonymizer = TextAnonymizer(store=store)
        self._context = ContextManager(store=store, anonymizer=self._anonymizer)
        self._llm: LLMClient = llm_client or GeminiClient(settings)
        self._anchor_date = anchor_date

    def _plan_intent(
        self,
        sanitized_question: str,
        context_packet: SanitizedContextPacket,
    ) -> IntentPlan:
        fast_plan = _deterministic_fast_intent(sanitized_question)
        if fast_plan is not None:
            return fast_plan

        if self._llm.is_configured():
            payload = {
                "question": sanitized_question,
                "context": asdict(context_packet),
            }
            return self._llm.generate_structured(_PLANNER_SYSTEM_PROMPT, payload, IntentPlan)

        return IntentPlan(
            domain="cross_domain",
            mode="catalog",
            days=_extract_days_from_text(sanitized_question, default_days=30),
            reasoning="offline_default_fallback",
        )

    def _execute_plan(self, plan: IntentPlan) -> CatalogResult:
        if plan.mode == "semantic_sql" and plan.semantic_sql:
            try:
                rows = self._repo.execute_semantic_sql(plan.semantic_sql)
                return CatalogResult(
                    domain=plan.domain,
                    source_view="SEMANTIC.CUSTOM_SELECT",
                    backend=self._repo.backend_name,
                    cache_hit=False,
                    start_date=None,
                    end_date=None,
                    rows=rows,
                    aggregates={"row_count": len(rows)},
                )
            except SQLGuardError:
                # Fall back safely to parameterized Level-1 catalog if generated SQL violates guard.
                pass

        start_date, end_date = self._catalog.default_range(
            days=plan.days, anchor_date=self._anchor_date
        )
        if plan.domain == "finance":
            return self._catalog.query_finance(
                start_date=start_date,
                end_date=end_date,
                direction=plan.direction,
                category=plan.category,
                discretionary_only=plan.discretionary_only,
            )
        if plan.domain == "habits_sleep":
            return self._catalog.query_habits_sleep(start_date=start_date, end_date=end_date)
        if plan.domain == "time_calendar":
            return self._catalog.query_time_calendar(
                start_date=start_date,
                end_date=end_date,
                domain_tag=plan.domain_tag,
                block_type=plan.block_type,
            )
        if plan.domain == "goals":
            return self._catalog.query_goals()
        return self._catalog.query_cross_domain(start_date=start_date, end_date=end_date)

    def ask(self, question: str, session_id: str | None = None) -> AskResult:
        """Process a user question end-to-end under strict masked_cloud privacy guarantees."""
        sid = session_id or str(uuid.uuid4())
        anon = self._anonymizer.anonymize(question, session_id=sid)
        context_packet = self._context.build_sanitized_packet(sid)

        plan = self._plan_intent(anon.sanitized_text, context_packet)
        catalog_result = self._execute_plan(plan)

        sample_rows = catalog_result.rows[:30]
        model_used = "deterministic_catalog"

        if self._llm.is_configured():
            synth_payload = self._anonymizer.anonymize_payload(
                {
                    "question": anon.sanitized_text,
                    "domain": catalog_result.domain,
                    "source_view": catalog_result.source_view,
                    "start_date": str(catalog_result.start_date)
                    if catalog_result.start_date
                    else None,
                    "end_date": str(catalog_result.end_date) if catalog_result.end_date else None,
                    "aggregates": catalog_result.aggregates,
                    "sample_rows": sample_rows,
                    "context": asdict(context_packet),
                },
                session_id=sid,
            )
            synthesized = self._llm.generate_structured(
                _SYNTHESIS_SYSTEM_PROMPT,
                synth_payload,
                SynthesizedAnswer,
            )
            model_used = self._llm.model_name
        else:
            synthesized = _format_deterministic_summary(plan, catalog_result)

        final_answer = self._anonymizer.rehydrate(synthesized.answer, session_id=sid)
        final_observations = [
            self._anonymizer.rehydrate(obs, session_id=sid) for obs in synthesized.key_observations
        ]

        # Store sanitized user turn in local history to avoid persisting redundant raw secrets.
        self._context.record_turn(sid, "user", anon.sanitized_text, model_used=None)
        self._context.record_turn(sid, "assistant", final_answer, model_used=model_used)

        evidence = EvidenceItem(
            source_view=catalog_result.source_view,
            backend=catalog_result.backend,
            cache_hit=catalog_result.cache_hit,
            start_date=catalog_result.start_date.isoformat() if catalog_result.start_date else None,
            end_date=catalog_result.end_date.isoformat() if catalog_result.end_date else None,
            row_count=len(catalog_result.rows),
        )

        return AskResult(
            session_id=sid,
            answer=final_answer,
            privacy_mode=self._settings.lup_privacy_mode,
            anonymizer_engine=self._anonymizer.engine_name,
            data_backend=catalog_result.backend,
            model_used=model_used,
            domain=catalog_result.domain,
            key_observations=final_observations,
            evidence=evidence,
            aggregates=catalog_result.aggregates,
            chart_series=sample_rows,
        )
