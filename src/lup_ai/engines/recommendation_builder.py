"""Grounded recommendation builder enforcing mandatory numeric evidence contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lup_ai.engines.anomaly_detector import AnomalyScanResult
from lup_ai.engines.simulation_engine import SimulationResult


class RecommendationEvidence(BaseModel):
    """Traceable numeric evidence supporting a recommendation."""

    domain: str
    source_view: str
    metric_name: str
    date_range: str
    baseline_value: float
    observed_value: float
    delta_or_z: str


class RecommendationPayload(BaseModel):
    """Strict output contract for every justified recommendation emitted by LUP AI."""

    recommendation_id: str
    domain: str
    title: str
    action: str
    expected_impact: str
    evidence: list[RecommendationEvidence] = Field(..., min_length=1)
    goal_alignment: str
    confidence: Literal["high", "medium", "low"]
    risks_and_tradeoffs: str
    constraint_checks: list[str] = Field(default_factory=list)


def _derive_confidence(sample_days: int) -> Literal["high", "medium", "low"]:
    if sample_days >= 60:
        return "high"
    if sample_days >= 21:
        return "medium"
    return "low"


class RecommendationBuilder:
    """Construct immutable evidence-backed recommendations from deterministic engine outputs."""

    def from_anomaly_scan(self, scan: AnomalyScanResult) -> list[RecommendationPayload]:
        """Build recommendations from top detected anomalies and opportunities."""
        date_range = f"{scan.start_date} a {scan.end_date}" if scan.start_date else "N/A"
        confidence = _derive_confidence(scan.total_days_scanned)
        items: list[RecommendationPayload] = []

        for anom in scan.anomalies[:2]:
            action = (
                f"Auditar el pico de {anom.metric_name} del {anom.detected_date} y definir un "
                f"limite preventivo cercano a {anom.expected_value}."
                if anom.actual_value > anom.expected_value
                else f"Corregir la caida en {anom.metric_name} hasta {anom.detected_date} "
                f"para volver sobre {anom.expected_value}."
            )
            items.append(
                RecommendationPayload(
                    recommendation_id=f"rec_{anom.anomaly_id}",
                    domain=anom.domain,
                    title=f"Atender anomalia en {anom.metric_name}",
                    action=action,
                    expected_impact=(
                        f"Retorno a linea base ({anom.expected_value} vs {anom.actual_value})."
                    ),
                    evidence=[
                        RecommendationEvidence(
                            domain=anom.domain,
                            source_view="SEMANTIC.VW_CROSS_DOMAIN_DAILY",
                            metric_name=anom.metric_name,
                            date_range=date_range,
                            baseline_value=anom.expected_value,
                            observed_value=anom.actual_value,
                            delta_or_z=f"Z_mod={anom.modified_z_score}",
                        )
                    ],
                    goal_alignment="Protege estabilidad y restricciones activas del dominio.",
                    confidence=confidence,
                    risks_and_tradeoffs=(
                        "Eventos excepcionales pueden requerir ajuste manual si fueron planeados."
                    ),
                    constraint_checks=["evaluated_against_hard_constraints"],
                )
            )

        for opp in scan.opportunities[:2]:
            items.append(
                RecommendationPayload(
                    recommendation_id=f"rec_{opp.opportunity_id}",
                    domain=opp.domain,
                    title=opp.title,
                    action=opp.description,
                    expected_impact=f"Ganancia estimada: +{opp.estimated_gain} {opp.unit}.",
                    evidence=[
                        RecommendationEvidence(
                            domain=opp.domain,
                            source_view="SEMANTIC.VW_CROSS_DOMAIN_DAILY",
                            metric_name=opp.metric_name,
                            date_range=date_range,
                            baseline_value=opp.baseline_value,
                            observed_value=opp.potential_value,
                            delta_or_z=f"+{opp.estimated_gain} {opp.unit}",
                        )
                    ],
                    goal_alignment="Acelera metas activas aprovechando holgura historica.",
                    confidence=confidence,
                    risks_and_tradeoffs=(
                        "Requiere adherencia sostenida; sobre-optimizar puede reducir descanso."
                    ),
                    constraint_checks=["no_hard_constraint_collision"],
                )
            )

        return items

    def from_simulation(self, sim: SimulationResult) -> list[RecommendationPayload]:
        """Build evidence-backed recommendations from a deterministic What-If simulation."""
        confidence = _derive_confidence(sim.baseline_days_used)
        constraints_msg = (
            [c.description for c in sim.constraint_collisions]
            if sim.constraint_collisions
            else ["Sin colisiones contra restricciones duras ni capacidad diaria."]
        )

        evidence_list: list[RecommendationEvidence] = []
        for name, traj in sim.trajectories.items():
            evidence_list.append(
                RecommendationEvidence(
                    domain="cross_domain",
                    source_view="SEMANTIC.VW_CROSS_DOMAIN_DAILY",
                    metric_name=name,
                    date_range=f"Sim {sim.horizon_days}d (base {sim.baseline_days_used}d)",
                    baseline_value=traj.baseline_projected,
                    observed_value=traj.base_p50,
                    delta_or_z=(
                        f"p25={traj.conservative_p25}, p75={traj.optimistic_p75} {traj.unit}"
                    ),
                )
            )

        sav = sim.trajectories.get("cumulative_savings")
        impact_text = (
            f"Ahorro proyectado p50={sav.base_p50} COP (rango p25={sav.conservative_p25} a "
            f"p75={sav.optimistic_p75}, delta={sav.delta_vs_baseline_p50} COP)."
            if sav
            else f"Proyeccion determinista a {sim.horizon_days} dias completada."
        )

        goal_summary = (
            "; ".join(
                f"{g.title}: {g.probability_of_success_pct}% ({g.status_label})"
                for g in sim.goal_projections
            )
            or "Evaluado contra parametros del escenario."
        )

        top_lever = (
            sim.sensitivity_ranking[0].lever_name if sim.sensitivity_ranking else "escenario_base"
        )
        action = (
            "Ajustar escenario para eliminar colisiones de restricciones antes de ejecutar."
            if any(c.severity == "blocking" for c in sim.constraint_collisions)
            else f"Priorizar palanca de impacto ({top_lever}) por {sim.horizon_days} dias."
        )

        return [
            RecommendationPayload(
                recommendation_id=f"rec_sim_{sim.horizon_days}d_{sim.scenario.seed}",
                domain="cross_domain",
                title=f"Plan de accion simulado a {sim.horizon_days} dias",
                action=action,
                expected_impact=impact_text,
                evidence=evidence_list,
                goal_alignment=goal_summary,
                confidence=confidence,
                risks_and_tradeoffs=(
                    "Asume varianza historica constante y cumplimiento real de las palancas."
                ),
                constraint_checks=constraints_msg,
            )
        ]
