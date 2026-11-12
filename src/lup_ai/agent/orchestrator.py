"""Central orchestrator wiring Python anonymizer, deterministic engines, and Gemini 3.8."""

from __future__ import annotations

import logging
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
from lup_ai.engines.anomaly_detector import AnomalyDetector, AnomalyScanResult
from lup_ai.engines.recommendation_builder import RecommendationBuilder, RecommendationPayload
from lup_ai.engines.simulation_engine import (
    SimulationEngine,
    SimulationResult,
    SimulationScenario,
)
from lup_ai.engines.trend_analyzer import TrendAnalysisResult, TrendAnalyzer
from lup_ai.llm.gemini_client import GeminiClient, IntentPlan, LLMClient, SynthesizedAnswer
from lup_ai.memory.context_manager import ContextManager, SanitizedContextPacket
from lup_ai.memory.decision_tracker import DecisionTracker
from lup_ai.privacy.text_anonymizer import PrivacyViolationError, TextAnonymizer

_LOGGER = logging.getLogger(__name__)

_PLANNER_SYSTEM_PROMPT = (
    "You are LUP AI query planner. Select analysis_type ('summary', 'trends', 'anomalies', "
    "'simulation'), domain, and either catalog filters, simulation_params, or a read-only "
    "Snowflake SELECT over LUP_AI_DB.SEMANTIC views: VW_FINANCE_SUMMARY, "
    "VW_HABITS_SLEEP_SUMMARY, VW_TIME_CALENDAR_SUMMARY, VW_CROSS_DOMAIN_DAILY, "
    "VW_GOAL_PROGRESS. Never perform arithmetic or reference RAW/CORE tables."
)

_SYNTHESIS_SYSTEM_PROMPT = (
    "You are LUP AI personal decision analyst. Explain ONLY the numeric metrics, anomalies, "
    "simulation percentiles, and evidence provided in the JSON payload. Never invent numbers, "
    "dates, or tables. Distinguish correlation from causation and align with active_goals "
    "and hard_constraints."
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
    analysis_type: str = "summary"
    recommendations: list[RecommendationPayload] = field(default_factory=list)
    trends: TrendAnalysisResult | None = None
    anomaly_scan: AnomalyScanResult | None = None
    simulation: SimulationResult | None = None


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


def _extract_simulation_scenario(text: str) -> SimulationScenario:
    """Extract deterministic What-If levers from natural-language text."""
    lowered = text.lower()

    horizon_days = 90
    months_match = re.search(r"\b(\d{1,2})\s*(?:mes|meses|months?)\b", lowered)
    if months_match:
        horizon_days = max(14, min(365, int(months_match.group(1)) * 30))
    else:
        days_match = re.search(r"\b(\d{2,3})\s*(?:dias|días|days)\b", lowered)
        if days_match:
            horizon_days = max(14, min(365, int(days_match.group(1))))

    disc_delta = 0.0
    spend_red = re.search(
        r"(?:reduz\w*|recort\w*|baj\w*|disminu\w*)\s+[^%\n]{0,40}?"
        r"(?:gastos?|ocio|discrecional\w*|domicilio\w*|delivery|spend)"
        r"[^%\n]{0,20}?(\d{1,2})\s*%",
        lowered,
    )
    if spend_red:
        disc_delta = -min(95, int(spend_red.group(1))) / 100.0
    else:
        pct_match = re.search(r"(-?\d{1,2})\s*%\s*(?:en\s+)?(?:gastos?|ocio|discrecional)", lowered)
        if pct_match:
            val = abs(int(pct_match.group(1)))
            if any(w in lowered for w in ("reduz", "recort", "baj", "menos")):
                disc_delta = -min(95, val) / 100.0

    sleep_target: float | None = None
    sleep_match = re.search(
        r"(?:duerm\w*|dormir|sueno|sueño)\s*(?:de|a|al\s+menos)?\s*(\d(?:\.\d)?)\s*h",
        lowered,
    )
    if sleep_match:
        sleep_target = max(3.0, min(12.0, float(sleep_match.group(1))))

    deep_work_delta = 0.0
    dw_match = re.search(
        r"(?:liber\w*|sum\w*|agreg\w*|aument\w*)\s*(\d{1,2}(?:\.\d)?)\s*h"
        r"(?:\s*(?:semanal\w*|a\s+la\s+semana|por\s+semana))?",
        lowered,
    )
    if dw_match:
        deep_work_delta = min(40.0, float(dw_match.group(1)))

    extra_savings = 0.0
    sav_match = re.search(
        r"ahorr\w*\s*(\d{4,7})\s*(?:cop|pesos)?\s*(?:extra|mas|más|semanal\w*)",
        lowered,
    )
    if sav_match:
        extra_savings = float(sav_match.group(1))

    return SimulationScenario(
        horizon_days=horizon_days,
        discretionary_spend_delta_pct=disc_delta,
        extra_weekly_savings=extra_savings,
        sleep_hours_target=sleep_target,
        deep_work_delta_hours_per_week=deep_work_delta,
    )


def _deterministic_fast_intent(sanitized_question: str) -> IntentPlan | None:
    """Resolve clear queries and analytical workflows without burning planning tokens."""
    text = sanitized_question.lower()
    days = _extract_days_from_text(text, default_days=30)

    is_sim = any(
        w in text
        for w in (
            "simula",
            "que pasa si",
            "qué pasa si",
            "si reduzco",
            "si ahorro",
            "si duermo",
            "escenario",
            "what-if",
            "what if",
        )
    )
    if is_sim:
        return IntentPlan(
            domain="cross_domain",
            mode="catalog",
            analysis_type="simulation",
            days=180,
            simulation_params=_extract_simulation_scenario(sanitized_question),
            reasoning="deterministic_what_if_simulation",
        )

    is_anomaly = any(
        w in text
        for w in (
            "anomalia",
            "anomalía",
            "anomalias",
            "anomalías",
            "pico",
            "picos",
            "oportunidad",
            "oportunidades",
            "alerta",
            "alertas",
            "desvio",
            "desvío",
        )
    )
    if is_anomaly:
        return IntentPlan(
            domain="cross_domain",
            mode="catalog",
            analysis_type="anomalies",
            days=max(days, 90),
            reasoning="deterministic_anomalies",
        )

    is_trend = any(
        w in text
        for w in (
            "tendencia",
            "tendencias",
            "comportamiento",
            "patron",
            "patrón",
            "patrones",
            "correlacion",
            "correlación",
            "estacionalidad",
        )
    )
    if is_trend:
        return IntentPlan(
            domain="cross_domain",
            mode="catalog",
            analysis_type="trends",
            days=max(days, 90),
            reasoning="deterministic_trends",
        )

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
    if domain_hits >= 2 or any(w in text for w in ("cruzad", "general", "resumen completo")):
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


def _format_deterministic_summary(result: CatalogResult) -> SynthesizedAnswer:
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


def _format_trends_summary(analysis: TrendAnalysisResult) -> SynthesizedAnswer:
    moving = [t for t in analysis.trends if t.direction != "stable"][:3]
    obs = [
        f"{t.metric_name}: 7d={t.mean_7d}, 30d={t.mean_30d}, direccion={t.direction}"
        for t in (moving or analysis.trends[:3])
    ]
    if analysis.correlations:
        top_corr = max(analysis.correlations, key=lambda c: abs(c.spearman_rho))
        obs.append(
            f"Correlacion ({top_corr.source_metric} -> {top_corr.target_metric}, "
            f"lag={top_corr.lag_days}d): rho={top_corr.spearman_rho} (no causal)"
        )
    answer = (
        f"Analisis de tendencias ({analysis.start_date} a {analysis.end_date}, "
        f"{analysis.total_days} dias): {len(analysis.trends)} metricas y "
        f"{len(analysis.correlations)} correlaciones evaluadas deterministamente."
    )
    return SynthesizedAnswer(answer=answer, key_observations=obs)


def _format_anomalies_summary(
    scan: AnomalyScanResult,
    recommendations: list[RecommendationPayload],
) -> SynthesizedAnswer:
    obs = [f"[{a.severity}] {a.detected_date} - {a.description}" for a in scan.anomalies[:3]]
    obs.extend(
        f"Oportunidad: {o.title} (+{o.estimated_gain} {o.unit})" for o in scan.opportunities[:2]
    )
    answer = (
        f"Escaneo ({scan.start_date} a {scan.end_date}): "
        f"{len(scan.anomalies)} anomalias, {len(scan.opportunities)} oportunidades y "
        f"{len(recommendations)} recomendaciones justificadas."
    )
    return SynthesizedAnswer(answer=answer, key_observations=obs)


def _format_simulation_summary(
    sim: SimulationResult,
    recommendations: list[RecommendationPayload],
) -> SynthesizedAnswer:
    sav = sim.trajectories.get("cumulative_savings")
    obs = [
        (
            f"Meta {g.title}: p50={g.projected_p50} {g.unit}, "
            f"prob={g.probability_of_success_pct}% ({g.status_label})"
        )
        for g in sim.goal_projections
    ]
    for col in sim.constraint_collisions:
        obs.append(f"Colision [{col.severity}]: {col.description}")
    if recommendations:
        obs.append(f"Accion sugerida: {recommendations[0].action}")
    sav_text = (
        f"ahorro proyectado p50={sav.base_p50} COP (p25={sav.conservative_p25}, "
        f"p75={sav.optimistic_p75}, delta={sav.delta_vs_baseline_p50} COP)"
        if sav
        else "trayectorias calculadas"
    )
    answer = (
        f"Simulacion What-If a {sim.horizon_days} dias (base {sim.baseline_days_used}d, "
        f"{sim.iterations} iteraciones): {sav_text}."
    )
    return SynthesizedAnswer(answer=answer, key_observations=obs)


class LupOrchestrator:
    """Coordinate masked_cloud privacy boundary, deterministic engines, and Gemini synthesis."""

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
        self._trend_analyzer = TrendAnalyzer()
        self._anomaly_detector = AnomalyDetector()
        self._sim_engine = SimulationEngine()
        self._rec_builder = RecommendationBuilder()
        self._decision_tracker = DecisionTracker(store)

    def _load_goals_and_prefs(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            goals = self._catalog.query_goals().rows
        except FileNotFoundError:
            goals = []
        if not goals:
            goals = self._store.list_active_goals()
        prefs = self._store.list_preferences()
        return goals, prefs

    def analyze_trends(self, days: int = 90) -> tuple[TrendAnalysisResult, EvidenceItem]:
        """Run deterministic TrendAnalyzer over cross-domain daily rows."""
        start_date, end_date = self._catalog.default_range(days=days, anchor_date=self._anchor_date)
        cross = self._catalog.query_cross_domain(start_date=start_date, end_date=end_date)
        analysis = self._trend_analyzer.analyze(cross.rows)
        evidence = EvidenceItem(
            source_view=cross.source_view,
            backend=cross.backend,
            cache_hit=cross.cache_hit,
            start_date=cross.start_date.isoformat() if cross.start_date else None,
            end_date=cross.end_date.isoformat() if cross.end_date else None,
            row_count=len(cross.rows),
        )
        return analysis, evidence

    def scan_anomalies(
        self, days: int = 90
    ) -> tuple[AnomalyScanResult, list[RecommendationPayload], EvidenceItem]:
        """Run deterministic AnomalyDetector and RecommendationBuilder."""
        start_date, end_date = self._catalog.default_range(days=days, anchor_date=self._anchor_date)
        cross = self._catalog.query_cross_domain(start_date=start_date, end_date=end_date)
        goals, prefs = self._load_goals_and_prefs()
        scan = self._anomaly_detector.scan(cross.rows, preferences=prefs, goals=goals)
        recommendations = self._rec_builder.from_anomaly_scan(scan)
        evidence = EvidenceItem(
            source_view=cross.source_view,
            backend=cross.backend,
            cache_hit=cross.cache_hit,
            start_date=cross.start_date.isoformat() if cross.start_date else None,
            end_date=cross.end_date.isoformat() if cross.end_date else None,
            row_count=len(cross.rows),
        )
        return scan, recommendations, evidence

    def simulate(
        self, scenario: SimulationScenario, baseline_days: int = 180
    ) -> tuple[SimulationResult, list[RecommendationPayload], EvidenceItem]:
        """Run deterministic What-If SimulationEngine and RecommendationBuilder."""
        start_date, end_date = self._catalog.default_range(
            days=baseline_days, anchor_date=self._anchor_date
        )
        cross = self._catalog.query_cross_domain(start_date=start_date, end_date=end_date)
        goals, prefs = self._load_goals_and_prefs()
        sim_result = self._sim_engine.simulate(
            daily_rows=cross.rows,
            scenario=scenario,
            goals=goals,
            preferences=prefs,
        )
        recommendations = self._rec_builder.from_simulation(sim_result)
        evidence = EvidenceItem(
            source_view=cross.source_view,
            backend=cross.backend,
            cache_hit=cross.cache_hit,
            start_date=cross.start_date.isoformat() if cross.start_date else None,
            end_date=cross.end_date.isoformat() if cross.end_date else None,
            row_count=len(cross.rows),
        )
        return sim_result, recommendations, evidence

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
            try:
                return self._llm.generate_structured(_PLANNER_SYSTEM_PROMPT, payload, IntentPlan)
            except PrivacyViolationError:
                raise
            except Exception as exc:
                _LOGGER.warning(
                    "Gemini intent planning unavailable, falling back: %s",
                    type(exc).__name__,
                )

        return IntentPlan(
            domain="cross_domain",
            mode="catalog",
            days=_extract_days_from_text(sanitized_question, default_days=30),
            reasoning="offline_default_fallback",
        )

    def _execute_catalog_plan(self, plan: IntentPlan) -> CatalogResult:
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

    def _synthesize_with_gemini_or_fallback(
        self,
        session_id: str,
        sanitized_question: str,
        context_packet: SanitizedContextPacket,
        payload_data: dict[str, Any],
        fallback_answer: SynthesizedAnswer,
    ) -> tuple[SynthesizedAnswer, str]:
        if not self._llm.is_configured():
            return fallback_answer, "deterministic_catalog"

        synth_payload = self._anonymizer.anonymize_payload(
            {
                "question": sanitized_question,
                "context": asdict(context_packet),
                **payload_data,
            },
            session_id=session_id,
        )
        try:
            synthesized = self._llm.generate_structured(
                _SYNTHESIS_SYSTEM_PROMPT,
                synth_payload,
                SynthesizedAnswer,
            )
            used_model = getattr(self._llm, "last_used_model", self._llm.model_name)
            return synthesized, used_model
        except PrivacyViolationError:
            raise
        except Exception as exc:
            _LOGGER.warning("Gemini synthesis unavailable, falling back: %s", type(exc).__name__)
            fallback_with_notice = SynthesizedAnswer(
                answer=fallback_answer.answer,
                key_observations=[
                    *fallback_answer.key_observations,
                    (
                        "Degradación controlada: Gemini no estuvo disponible "
                        f"({type(exc).__name__}); se muestra resumen determinista."
                    ),
                ],
                goal_alignment_note=fallback_answer.goal_alignment_note,
            )
            return fallback_with_notice, "deterministic_fallback"

    def ask(self, question: str, session_id: str | None = None) -> AskResult:
        """Process a user question end-to-end under strict masked_cloud privacy guarantees."""
        sid = session_id or str(uuid.uuid4())
        anon = self._anonymizer.anonymize(question, session_id=sid)
        context_packet = self._context.build_sanitized_packet(sid)
        plan = self._plan_intent(anon.sanitized_text, context_packet)

        trends_res: TrendAnalysisResult | None = None
        scan_res: AnomalyScanResult | None = None
        sim_res: SimulationResult | None = None
        recommendations: list[RecommendationPayload] = []
        chart_series: list[dict[str, Any]] = []
        aggregates: dict[str, Any] = {}
        domain = plan.domain

        if plan.analysis_type == "trends":
            trends_res, evidence = self.analyze_trends(days=max(plan.days, 30))
            aggregates = {
                "total_days": trends_res.total_days,
                "trends_count": len(trends_res.trends),
            }
            fallback = _format_trends_summary(trends_res)
            synthesized, model_used = self._synthesize_with_gemini_or_fallback(
                sid,
                anon.sanitized_text,
                context_packet,
                {"analysis_type": "trends", "trends_report": trends_res.model_dump()},
                fallback,
            )
            domain = "cross_domain"

        elif plan.analysis_type == "anomalies":
            scan_res, recommendations, evidence = self.scan_anomalies(days=max(plan.days, 60))
            aggregates = {
                "anomalies_count": len(scan_res.anomalies),
                "opportunities_count": len(scan_res.opportunities),
            }
            fallback = _format_anomalies_summary(scan_res, recommendations)
            synthesized, model_used = self._synthesize_with_gemini_or_fallback(
                sid,
                anon.sanitized_text,
                context_packet,
                {
                    "analysis_type": "anomalies",
                    "anomaly_scan": scan_res.model_dump(),
                    "recommendations": [r.model_dump() for r in recommendations],
                },
                fallback,
            )
            domain = "cross_domain"

        elif plan.analysis_type == "simulation":
            scenario = plan.simulation_params or _extract_simulation_scenario(anon.sanitized_text)
            sim_res, recommendations, evidence = self.simulate(scenario, baseline_days=180)
            aggregates = {name: traj.base_p50 for name, traj in sim_res.trajectories.items()}
            fallback = _format_simulation_summary(sim_res, recommendations)
            synthesized, model_used = self._synthesize_with_gemini_or_fallback(
                sid,
                anon.sanitized_text,
                context_packet,
                {
                    "analysis_type": "simulation",
                    "simulation": sim_res.model_dump(),
                    "recommendations": [r.model_dump() for r in recommendations],
                },
                fallback,
            )
            domain = "cross_domain"

        else:
            catalog_result = self._execute_catalog_plan(plan)
            chart_series = catalog_result.rows[:30]
            aggregates = catalog_result.aggregates
            evidence = EvidenceItem(
                source_view=catalog_result.source_view,
                backend=catalog_result.backend,
                cache_hit=catalog_result.cache_hit,
                start_date=catalog_result.start_date.isoformat()
                if catalog_result.start_date
                else None,
                end_date=catalog_result.end_date.isoformat() if catalog_result.end_date else None,
                row_count=len(catalog_result.rows),
            )
            fallback = _format_deterministic_summary(catalog_result)
            synthesized, model_used = self._synthesize_with_gemini_or_fallback(
                sid,
                anon.sanitized_text,
                context_packet,
                {
                    "domain": catalog_result.domain,
                    "source_view": catalog_result.source_view,
                    "start_date": evidence.start_date,
                    "end_date": evidence.end_date,
                    "aggregates": catalog_result.aggregates,
                    "sample_rows": chart_series,
                },
                fallback,
            )
            domain = catalog_result.domain

        final_answer = self._anonymizer.rehydrate(synthesized.answer, session_id=sid)
        final_observations = [
            self._anonymizer.rehydrate(obs, session_id=sid) for obs in synthesized.key_observations
        ]

        if recommendations:
            self._decision_tracker.persist_recommendations(
                recommendations=recommendations,
                source_type="interactive",
                model_used=model_used,
                prompt_summary=anon.sanitized_text,
            )

        self._context.record_turn(sid, "user", anon.sanitized_text, model_used=None)
        self._context.record_turn(sid, "assistant", final_answer, model_used=model_used)

        return AskResult(
            session_id=sid,
            answer=final_answer,
            privacy_mode=self._settings.lup_privacy_mode,
            anonymizer_engine=self._anonymizer.engine_name,
            data_backend=evidence.backend,
            model_used=model_used,
            domain=domain,
            key_observations=final_observations,
            evidence=evidence,
            aggregates=aggregates,
            chart_series=chart_series,
            analysis_type=plan.analysis_type,
            recommendations=recommendations,
            trends=trends_res,
            anomaly_scan=scan_res,
            simulation=sim_res,
        )
