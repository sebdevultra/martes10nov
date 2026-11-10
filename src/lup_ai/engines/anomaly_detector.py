"""Robust deterministic detector for cross-domain anomalies and actionable opportunities."""

from __future__ import annotations

import json
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field


class AnomalyItem(BaseModel):
    """Statistical or hard-constraint anomaly detected deterministically."""

    anomaly_id: str
    detected_date: str
    domain: str
    metric_name: str
    expected_value: float
    actual_value: float
    modified_z_score: float
    severity: Literal["low", "medium", "high"]
    detection_method: str
    description: str


class OpportunityItem(BaseModel):
    """Actionable positive slack or cross-domain leverage opportunity."""

    opportunity_id: str
    domain: str
    title: str
    metric_name: str
    baseline_value: float
    potential_value: float
    estimated_gain: float
    unit: str
    description: str


class AnomalyScanResult(BaseModel):
    """Combined anomaly and opportunity scan report."""

    start_date: str | None = None
    end_date: str | None = None
    total_days_scanned: int
    anomalies: list[AnomalyItem] = Field(default_factory=list)
    opportunities: list[OpportunityItem] = Field(default_factory=list)


def _extract_min_sleep_constraint(preferences: list[dict[str, Any]] | None) -> float:
    if not preferences:
        return 6.5
    for pref in preferences:
        if pref.get("key_name") == "min_sleep_hours":
            try:
                parsed = (
                    json.loads(pref["value_json"])
                    if isinstance(pref["value_json"], str)
                    else pref["value_json"]
                )
                return float(parsed.get("min_hours", 6.5))
            except Exception:
                return 6.5
    return 6.5


def _detect_series_spikes(
    df: pd.DataFrame,
    domain: str,
    metric: str,
    direction: Literal["high", "low"],
    z_threshold: float = 3.2,
    positive_only_baseline: bool = False,
) -> list[AnomalyItem]:
    if metric not in df.columns:
        return []

    series = pd.to_numeric(df[metric], errors="coerce")
    baseline_vals = series[series > 0].dropna() if positive_only_baseline else series.dropna()
    if len(baseline_vals) < 10:
        return []

    median = float(baseline_vals.median())
    mad = float(np.median(np.abs(baseline_vals.to_numpy(dtype=float) - median)))
    q1 = float(baseline_vals.quantile(0.25))
    q3 = float(baseline_vals.quantile(0.75))
    iqr = q3 - q1

    if mad > 1e-6:
        scale = mad / 0.6745
    elif iqr > 1e-6:
        scale = iqr / 1.349
    else:
        std = float(baseline_vals.std())
        if std <= 1e-6:
            return []
        scale = std

    upper_fence = q3 + 1.5 * iqr if iqr > 1e-6 else median + 3.0 * scale
    lower_fence = q1 - 1.5 * iqr if iqr > 1e-6 else median - 3.0 * scale

    items: list[AnomalyItem] = []
    for idx, val in series.items():
        if pd.isna(val):
            continue
        val_f = float(val)
        if positive_only_baseline and val_f <= 0:
            continue

        z_mod = (val_f - median) / scale
        is_outlier = (direction == "high" and z_mod >= z_threshold and val_f > upper_fence) or (
            direction == "low" and z_mod <= -z_threshold and val_f < lower_fence
        )
        if not is_outlier:
            continue

        abs_z = abs(z_mod)
        severity: Literal["low", "medium", "high"] = (
            "high" if abs_z >= 5.0 else ("medium" if abs_z >= 3.8 else "low")
        )
        dt_str = df.loc[idx, "snapshot_date"].strftime("%Y-%m-%d")
        items.append(
            AnomalyItem(
                anomaly_id=f"anom_{domain}_{metric}_{dt_str}",
                detected_date=dt_str,
                domain=domain,
                metric_name=metric,
                expected_value=round(median, 2),
                actual_value=round(val_f, 2),
                modified_z_score=round(z_mod, 2),
                severity=severity,
                detection_method="mad_iqr",
                description=(
                    f"{metric} ({round(val_f, 2)}) desvio respecto a mediana ({round(median, 2)}) "
                    f"con Z-modificado={round(z_mod, 2)}."
                ),
            )
        )
    return items


def _detect_sleep_streaks(
    df: pd.DataFrame,
    min_sleep_hours: float,
    min_streak_days: int = 3,
) -> list[AnomalyItem]:
    if "sleep_hours" not in df.columns:
        return []

    items: list[AnomalyItem] = []
    streak_dates: list[str] = []
    streak_vals: list[float] = []

    def flush_streak() -> None:
        if len(streak_dates) < min_streak_days:
            return
        avg_val = round(sum(streak_vals) / len(streak_vals), 2)
        items.append(
            AnomalyItem(
                anomaly_id=f"anom_sleep_streak_{streak_dates[0]}",
                detected_date=streak_dates[-1],
                domain="habits_sleep",
                metric_name="sleep_hours_streak",
                expected_value=min_sleep_hours,
                actual_value=avg_val,
                modified_z_score=round((avg_val - min_sleep_hours) / 0.5, 2),
                severity="high" if len(streak_dates) >= 4 else "medium",
                detection_method="hard_constraint_streak",
                description=(
                    f"Racha de {len(streak_dates)} noches consecutivas por debajo de "
                    f"{min_sleep_hours}h (promedio={avg_val}h, {streak_dates[0]} a "
                    f"{streak_dates[-1]})."
                ),
            )
        )

    for _, row in df.iterrows():
        val = row.get("sleep_hours")
        dt_str = row["snapshot_date"].strftime("%Y-%m-%d")
        if pd.notna(val) and float(val) < min_sleep_hours:
            streak_dates.append(dt_str)
            streak_vals.append(float(val))
        else:
            flush_streak()
            streak_dates.clear()
            streak_vals.clear()
    flush_streak()
    return items


class AnomalyDetector:
    """Detect statistical outliers, constraint streaks, and positive opportunities."""

    def scan(
        self,
        daily_rows: list[dict[str, Any]],
        preferences: list[dict[str, Any]] | None = None,
        goals: list[dict[str, Any]] | None = None,
    ) -> AnomalyScanResult:
        """Scan daily cross-domain rows for anomalies and improvement opportunities."""
        if not daily_rows:
            return AnomalyScanResult(total_days_scanned=0)

        df = pd.DataFrame(daily_rows).copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df = df.sort_values("snapshot_date").reset_index(drop=True)

        min_sleep = _extract_min_sleep_constraint(preferences)

        anomalies: list[AnomalyItem] = []
        anomalies.extend(
            _detect_series_spikes(
                df,
                domain="finance",
                metric="discretionary_spend",
                direction="high",
                z_threshold=3.2,
                positive_only_baseline=True,
            )
        )
        anomalies.extend(
            _detect_series_spikes(
                df,
                domain="habits_sleep",
                metric="sleep_hours",
                direction="low",
                z_threshold=2.8,
                positive_only_baseline=False,
            )
        )
        anomalies.extend(
            _detect_series_spikes(
                df,
                domain="time_calendar",
                metric="meeting_hours",
                direction="high",
                z_threshold=3.2,
                positive_only_baseline=True,
            )
        )
        anomalies.extend(_detect_sleep_streaks(df, min_sleep_hours=min_sleep))

        anomalies.sort(key=lambda item: (item.detected_date, item.severity), reverse=True)
        opportunities = self._detect_opportunities(df, goals=goals)

        return AnomalyScanResult(
            start_date=df["snapshot_date"].iloc[0].strftime("%Y-%m-%d"),
            end_date=df["snapshot_date"].iloc[-1].strftime("%Y-%m-%d"),
            total_days_scanned=len(df),
            anomalies=anomalies,
            opportunities=opportunities,
        )

    @staticmethod
    def _detect_opportunities(
        df: pd.DataFrame,
        goals: list[dict[str, Any]] | None = None,
    ) -> list[OpportunityItem]:
        opportunities: list[OpportunityItem] = []
        recent = df.tail(min(30, len(df)))

        if {"net_cashflow", "allocated_savings"} <= set(recent.columns):
            net_sum = float(recent["net_cashflow"].sum())
            saved_sum = float(recent["allocated_savings"].sum())
            unallocated = net_sum - saved_sum
            if unallocated > 100000.0:
                opportunities.append(
                    OpportunityItem(
                        opportunity_id="opp_finance_unallocated_surplus",
                        domain="finance",
                        title="Excedente de flujo asignable a ahorro",
                        metric_name="unallocated_cashflow_30d",
                        baseline_value=round(saved_sum, 2),
                        potential_value=round(saved_sum + unallocated * 0.5, 2),
                        estimated_gain=round(unallocated * 0.5, 2),
                        unit="COP",
                        description=(
                            f"En los ultimos {len(recent)} dias hubo excedente neto no asignado "
                            f"de {round(unallocated, 2)} COP; redirigir 50% acelera el ahorro."
                        ),
                    )
                )

        if {"meeting_hours", "deep_work_hours"} <= set(recent.columns):
            weekdays = recent[recent["snapshot_date"].dt.day_of_week < 5]
            low_meet_days = weekdays[
                (weekdays["meeting_hours"] <= 1.5) & (weekdays["deep_work_hours"] < 3.0)
            ]
            if len(low_meet_days) >= 3:
                avg_dw = float(low_meet_days["deep_work_hours"].mean())
                gain_hours = round(len(low_meet_days) * max(0.0, 3.0 - avg_dw), 2)
                opportunities.append(
                    OpportunityItem(
                        opportunity_id="opp_time_deep_work_capacity",
                        domain="time_calendar",
                        title="Ventanas de baja carga de reuniones aprovechables",
                        metric_name="recoverable_deep_work_hours_30d",
                        baseline_value=round(float(weekdays["deep_work_hours"].sum()), 2),
                        potential_value=round(
                            float(weekdays["deep_work_hours"].sum()) + gain_hours, 2
                        ),
                        estimated_gain=gain_hours,
                        unit="hours",
                        description=(
                            f"Se detectaron {len(low_meet_days)} dias laborales con <=1.5h de "
                            f"reuniones y <3h de deep work ({gain_hours}h recuperables)."
                        ),
                    )
                )

        if {"sleep_hours", "discretionary_spend"} <= set(df.columns) and len(df) >= 20:
            shifted = df.copy()
            shifted["next_day_disc"] = shifted["discretionary_spend"].shift(-1)
            well_rested = shifted[shifted["sleep_hours"] >= 7.5]["next_day_disc"].dropna()
            short_sleep = shifted[shifted["sleep_hours"] < 6.5]["next_day_disc"].dropna()
            if len(well_rested) >= 5 and len(short_sleep) >= 3:
                avg_rested = float(well_rested.mean())
                avg_short = float(short_sleep.mean())
                if avg_short > avg_rested * 1.15:
                    opportunities.append(
                        OpportunityItem(
                            opportunity_id="opp_cross_sleep_spend_leverage",
                            domain="cross_domain",
                            title="Menor gasto discrecional tras noches de >=7.5h de sueno",
                            metric_name="next_day_discretionary_spend",
                            baseline_value=round(avg_short, 2),
                            potential_value=round(avg_rested, 2),
                            estimated_gain=round(avg_short - avg_rested, 2),
                            unit="COP/day",
                            description=(
                                f"Tras noches <6.5h el gasto discrecional siguiente promedio fue "
                                f"{round(avg_short, 2)} COP vs {round(avg_rested, 2)} COP con "
                                f">=7.5h de sueno (correlacion observada, no causal)."
                            ),
                        )
                    )

        return opportunities
