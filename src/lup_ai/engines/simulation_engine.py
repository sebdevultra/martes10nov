"""Deterministic What-If and Monte Carlo simulation engine across all 3 domains."""

from __future__ import annotations

import json
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


class SimulationScenario(BaseModel):
    """Input levers and horizon for deterministic What-If projections."""

    horizon_days: int = Field(default=90, ge=14, le=365)
    discretionary_spend_delta_pct: float = Field(default=0.0, ge=-0.95, le=2.0)
    extra_weekly_savings: float = Field(default=0.0, ge=-2000000.0, le=10000000.0)
    sleep_hours_target: float | None = Field(default=None, ge=3.0, le=12.0)
    deep_work_delta_hours_per_week: float = Field(default=0.0, ge=-30.0, le=40.0)
    meeting_delta_hours_per_week: float = Field(default=0.0, ge=-30.0, le=40.0)
    iterations: int = Field(default=1000, ge=100, le=5000)
    seed: int = 42


class TrajectoryStats(BaseModel):
    """Projected p25/p50/p75 outcomes vs baseline projection."""

    metric_name: str
    unit: str
    baseline_projected: float
    conservative_p25: float
    base_p50: float
    optimistic_p75: float
    delta_vs_baseline_p50: float


class GoalProjection(BaseModel):
    """Projected attainment probability for an active goal over the simulation horizon."""

    goal_id: str
    domain: str
    title: str
    metric_name: str
    target_value: float
    unit: str
    projected_p50: float
    probability_of_success_pct: float
    status_label: Literal["on_track", "at_risk", "off_track"]


class ConstraintCollision(BaseModel):
    """Hard-constraint or realistic physical capacity conflict detected in scenario."""

    pref_id: str
    key_name: str
    severity: Literal["warning", "blocking"]
    description: str


class SensitivityFactor(BaseModel):
    """Marginal impact estimate of an individual scenario lever."""

    lever_name: str
    target_metric: str
    estimated_impact_p50: float
    unit: str


class SimulationResult(BaseModel):
    """Complete deterministic What-If output consumable by API, CLI, and Streamlit."""

    horizon_days: int
    baseline_days_used: int
    iterations: int
    scenario: SimulationScenario
    trajectories: dict[str, TrajectoryStats] = Field(default_factory=dict)
    goal_projections: list[GoalProjection] = Field(default_factory=list)
    constraint_collisions: list[ConstraintCollision] = Field(default_factory=list)
    sensitivity_ranking: list[SensitivityFactor] = Field(default_factory=list)


def _extract_min_sleep_pref(preferences: list[dict[str, Any]] | None) -> float:
    if not preferences:
        return 6.5
    for pref in preferences:
        if pref.get("key_name") == "min_sleep_hours":
            try:
                raw = pref["value_json"]
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                return float(parsed.get("min_hours", 6.5))
            except Exception:
                return 6.5
    return 6.5


def _bootstrap_horizon_sums(
    daily_values: np.ndarray,
    horizon_days: int,
    iterations: int,
    rng: np.random.Generator,
) -> np.ndarray:
    if len(daily_values) == 0:
        return np.zeros(iterations, dtype=float)
    sample_idx = rng.integers(0, len(daily_values), size=(iterations, horizon_days))
    return daily_values[sample_idx].sum(axis=1)


def _bootstrap_horizon_means(
    daily_values: np.ndarray,
    horizon_days: int,
    iterations: int,
    rng: np.random.Generator,
) -> np.ndarray:
    if len(daily_values) == 0:
        return np.zeros(iterations, dtype=float)
    sample_idx = rng.integers(0, len(daily_values), size=(iterations, horizon_days))
    return daily_values[sample_idx].mean(axis=1)


def _percentiles(arr: np.ndarray) -> tuple[float, float, float]:
    p25, p50, p75 = np.percentile(arr, [25, 50, 75])
    return round(float(p25), 2), round(float(p50), 2), round(float(p75), 2)


class SimulationEngine:
    """Execute reproducible empirical-bootstrap What-If simulations over historical snapshots."""

    def simulate(
        self,
        daily_rows: list[dict[str, Any]],
        scenario: SimulationScenario,
        goals: list[dict[str, Any]] | None = None,
        preferences: list[dict[str, Any]] | None = None,
    ) -> SimulationResult:
        """Project conservative, base, and optimistic scenarios against goals and constraints."""
        if len(daily_rows) < 7:
            raise ValueError("At least 7 days of historical data are required to simulate.")

        df = pd.DataFrame(daily_rows).copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df = df.sort_values("snapshot_date").tail(min(180, len(df))).reset_index(drop=True)
        baseline_days = len(df)
        horizon_weeks = scenario.horizon_days / 7.0
        rng = np.random.default_rng(scenario.seed)

        disc_daily = (
            pd.to_numeric(df["discretionary_spend"], errors="coerce").fillna(0.0).to_numpy()
        )
        save_daily = pd.to_numeric(df["allocated_savings"], errors="coerce").fillna(0.0).to_numpy()
        sleep_daily = pd.to_numeric(df["sleep_hours"], errors="coerce").dropna().to_numpy()
        dw_daily = pd.to_numeric(df["deep_work_hours"], errors="coerce").fillna(0.0).to_numpy()
        sched_daily = (
            pd.to_numeric(df["total_scheduled_hours"], errors="coerce").fillna(0.0).to_numpy()
        )

        # Baseline projections (zero lever changes).
        base_disc_sims = _bootstrap_horizon_sums(
            disc_daily, scenario.horizon_days, scenario.iterations, rng
        )
        base_save_sims = _bootstrap_horizon_sums(
            save_daily, scenario.horizon_days, scenario.iterations, rng
        )
        base_sleep_sims = _bootstrap_horizon_means(
            sleep_daily, scenario.horizon_days, scenario.iterations, rng
        )
        base_dw_sims = _bootstrap_horizon_sums(
            dw_daily, scenario.horizon_days, scenario.iterations, rng
        )

        # Scenario adjustments: discretionary savings transfer partially into cumulative savings.
        disc_factor = 1.0 + scenario.discretionary_spend_delta_pct
        scen_disc_sims = base_disc_sims * disc_factor
        freed_from_disc = np.maximum(0.0, base_disc_sims - scen_disc_sims)
        scen_save_sims = (
            base_save_sims + (scenario.extra_weekly_savings * horizon_weeks) + freed_from_disc
        )

        if scenario.sleep_hours_target is not None and len(sleep_daily) > 0:
            sleep_shift = scenario.sleep_hours_target - float(np.mean(sleep_daily))
            scen_sleep_sims = np.clip(base_sleep_sims + sleep_shift, 3.0, 12.0)
        else:
            scen_sleep_sims = base_sleep_sims

        scen_dw_sims = np.maximum(
            0.0,
            base_dw_sims + (scenario.deep_work_delta_hours_per_week * horizon_weeks),
        )

        # Express weekly averages alongside horizon cumulative metrics.
        scen_weekly_save_sims = scen_save_sims / horizon_weeks
        scen_weekly_disc_sims = scen_disc_sims / horizon_weeks
        scen_weekly_dw_sims = scen_dw_sims / horizon_weeks

        trajectories: dict[str, TrajectoryStats] = {}
        specs = (
            ("cumulative_savings", "COP", base_save_sims, scen_save_sims),
            ("cumulative_discretionary_spend", "COP", base_disc_sims, scen_disc_sims),
            ("avg_sleep_hours", "hours", base_sleep_sims, scen_sleep_sims),
            (
                "weekly_deep_work_hours",
                "hours/week",
                base_dw_sims / horizon_weeks,
                scen_weekly_dw_sims,
            ),
        )
        for name, unit, base_arr, scen_arr in specs:
            base_med = round(float(np.median(base_arr)), 2)
            p25, p50, p75 = _percentiles(scen_arr)
            trajectories[name] = TrajectoryStats(
                metric_name=name,
                unit=unit,
                baseline_projected=base_med,
                conservative_p25=p25,
                base_p50=p50,
                optimistic_p75=p75,
                delta_vs_baseline_p50=round(p50 - base_med, 2),
            )

        # Check collisions against hard constraints and daily time budget.
        collisions: list[ConstraintCollision] = []
        min_sleep = _extract_min_sleep_pref(preferences)
        if scenario.sleep_hours_target is not None and scenario.sleep_hours_target < min_sleep:
            collisions.append(
                ConstraintCollision(
                    pref_id="pref_sleep_floor",
                    key_name="min_sleep_hours",
                    severity="blocking",
                    description=(
                        f"El objetivo de sueno simulado ({scenario.sleep_hours_target}h) viola la "
                        f"restriccion dura minima ({min_sleep}h)."
                    ),
                )
            )

        mean_sched = float(np.mean(sched_daily)) if len(sched_daily) else 0.0
        projected_daily_sched = mean_sched + (
            (scenario.deep_work_delta_hours_per_week + scenario.meeting_delta_hours_per_week) / 7.0
        )
        projected_sleep = (
            scenario.sleep_hours_target
            if scenario.sleep_hours_target is not None
            else (float(np.mean(sleep_daily)) if len(sleep_daily) else 7.0)
        )
        if projected_daily_sched + projected_sleep > 21.0:
            collisions.append(
                ConstraintCollision(
                    pref_id="system_daily_capacity",
                    key_name="daily_time_budget",
                    severity="warning",
                    description=(
                        f"Sueno ({round(projected_sleep, 1)}h) + bloques agendados "
                        f"({round(projected_daily_sched, 1)}h) supera 21h/dia; riesgo de fatiga."
                    ),
                )
            )

        # Evaluate probability of achieving active goals.
        metric_sim_map = {
            "weekly_allocated_savings": scen_weekly_save_sims,
            "weekly_discretionary_spend": scen_weekly_disc_sims,
            "avg_sleep_hours": scen_sleep_sims,
            "weekly_deep_work_hours": scen_weekly_dw_sims,
        }
        goal_projections: list[GoalProjection] = []
        for g in goals or []:
            if g.get("status", "active") != "active":
                continue
            metric_name = g["metric_name"]
            if metric_name not in metric_sim_map:
                continue
            sim_arr = metric_sim_map[metric_name]
            target = float(g["target_value"])
            op = g.get("comparison_operator", ">=")
            if op == "<=":
                prob = float(np.mean(sim_arr <= target)) * 100.0
            else:
                prob = float(np.mean(sim_arr >= target)) * 100.0
            p50_val = round(float(np.median(sim_arr)), 2)
            status_label: Literal["on_track", "at_risk", "off_track"] = (
                "on_track" if prob >= 70.0 else ("at_risk" if prob >= 40.0 else "off_track")
            )
            goal_projections.append(
                GoalProjection(
                    goal_id=g["goal_id"],
                    domain=g["domain"],
                    title=g["title"],
                    metric_name=metric_name,
                    target_value=target,
                    unit=g["unit"],
                    projected_p50=p50_val,
                    probability_of_success_pct=round(prob, 1),
                    status_label=status_label,
                )
            )

        # Sensitivity ranking across active scenario levers.
        sensitivity: list[SensitivityFactor] = []
        if abs(scenario.discretionary_spend_delta_pct) > 1e-6:
            disc_impact = float(np.median(freed_from_disc))
            sensitivity.append(
                SensitivityFactor(
                    lever_name="discretionary_spend_delta_pct",
                    target_metric="cumulative_savings",
                    estimated_impact_p50=round(disc_impact, 2),
                    unit="COP",
                )
            )
        if abs(scenario.extra_weekly_savings) > 1e-6:
            sensitivity.append(
                SensitivityFactor(
                    lever_name="extra_weekly_savings",
                    target_metric="cumulative_savings",
                    estimated_impact_p50=round(scenario.extra_weekly_savings * horizon_weeks, 2),
                    unit="COP",
                )
            )
        if scenario.sleep_hours_target is not None and len(sleep_daily) > 0:
            sensitivity.append(
                SensitivityFactor(
                    lever_name="sleep_hours_target",
                    target_metric="avg_sleep_hours",
                    estimated_impact_p50=round(
                        scenario.sleep_hours_target - float(np.mean(sleep_daily)), 2
                    ),
                    unit="hours/day",
                )
            )
        if abs(scenario.deep_work_delta_hours_per_week) > 1e-6:
            sensitivity.append(
                SensitivityFactor(
                    lever_name="deep_work_delta_hours_per_week",
                    target_metric="total_deep_work_hours",
                    estimated_impact_p50=round(
                        scenario.deep_work_delta_hours_per_week * horizon_weeks, 2
                    ),
                    unit="hours",
                )
            )
        sensitivity.sort(key=lambda s: abs(s.estimated_impact_p50), reverse=True)

        return SimulationResult(
            horizon_days=scenario.horizon_days,
            baseline_days_used=baseline_days,
            iterations=scenario.iterations,
            scenario=scenario,
            trajectories=trajectories,
            goal_projections=goal_projections,
            constraint_collisions=collisions,
            sensitivity_ranking=sensitivity,
        )
