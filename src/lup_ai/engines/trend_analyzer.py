"""Deterministic behavior, rolling trend, seasonality, and cross-domain correlation analyzer."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats

_TRACKED_METRICS: tuple[tuple[str, str], ...] = (
    ("finance", "total_spend"),
    ("finance", "discretionary_spend"),
    ("finance", "allocated_savings"),
    ("finance", "net_cashflow"),
    ("habits_sleep", "sleep_hours"),
    ("habits_sleep", "sleep_quality_score"),
    ("habits_sleep", "habit_completion_rate"),
    ("habits_sleep", "energy_score"),
    ("time_calendar", "deep_work_hours"),
    ("time_calendar", "meeting_hours"),
    ("time_calendar", "leisure_hours"),
)


class MetricTrend(BaseModel):
    """Deterministic rolling and directional trend summary for one metric."""

    domain: str
    metric_name: str
    sample_size: int
    mean_7d: float | None = None
    mean_30d: float | None = None
    mean_90d: float | None = None
    wow_delta_pct: float | None = None
    linear_slope_per_day: float | None = None
    r_squared: float | None = None
    direction: str = "stable"
    weekday_mean: float | None = None
    weekend_mean: float | None = None


class CorrelationItem(BaseModel):
    """Cross-domain statistical association (explicitly non-causal)."""

    source_metric: str
    target_metric: str
    lag_days: int
    spearman_rho: float
    p_value: float
    sample_size: int
    strength: str
    correlation_only: bool = True


class TrendAnalysisResult(BaseModel):
    """Complete deterministic behavior and trend report across all 3 domains."""

    start_date: str | None = None
    end_date: str | None = None
    total_days: int
    trends: list[MetricTrend] = Field(default_factory=list)
    correlations: list[CorrelationItem] = Field(default_factory=list)


def _safe_mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return round(float(clean.mean()), 4)


def _classify_direction(slope: float, baseline_std: float) -> str:
    if baseline_std <= 1e-9:
        return "stable"
    # Normalize daily slope over a 30-day window relative to typical spread.
    normalized_change = (slope * 30.0) / baseline_std
    if normalized_change > 0.35:
        return "upward"
    if normalized_change < -0.35:
        return "downward"
    return "stable"


def _compute_lagged_correlation(
    df: pd.DataFrame,
    source_col: str,
    target_col: str,
    lag_days: int,
) -> CorrelationItem | None:
    if source_col not in df.columns or target_col not in df.columns:
        return None
    paired = pd.DataFrame(
        {
            "src": pd.to_numeric(df[source_col], errors="coerce").shift(lag_days),
            "tgt": pd.to_numeric(df[target_col], errors="coerce"),
        }
    ).dropna()

    if len(paired) < 14:
        return None
    if paired["src"].nunique() < 2 or paired["tgt"].nunique() < 2:
        return None

    rho, p_val = stats.spearmanr(paired["src"], paired["tgt"])
    if np.isnan(rho) or np.isnan(p_val):
        return None

    abs_rho = abs(float(rho))
    if abs_rho >= 0.45:
        strength = "strong"
    elif abs_rho >= 0.25:
        strength = "moderate"
    else:
        strength = "weak"

    return CorrelationItem(
        source_metric=source_col,
        target_metric=target_col,
        lag_days=lag_days,
        spearman_rho=round(float(rho), 4),
        p_value=round(float(p_val), 4),
        sample_size=len(paired),
        strength=strength,
        correlation_only=True,
    )


class TrendAnalyzer:
    """Compute rolling averages, slopes, weekday/weekend splits, and cross-domain correlations."""

    def analyze(self, daily_rows: list[dict[str, Any]]) -> TrendAnalysisResult:
        """Analyze chronological daily snapshots from VW_CROSS_DOMAIN_DAILY."""
        if not daily_rows:
            return TrendAnalysisResult(total_days=0)

        df = pd.DataFrame(daily_rows).copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df = df.sort_values("snapshot_date").reset_index(drop=True)
        df["is_weekend"] = df["snapshot_date"].dt.day_of_week >= 5

        trends: list[MetricTrend] = []
        for domain, metric in _TRACKED_METRICS:
            if metric not in df.columns:
                continue
            series = pd.to_numeric(df[metric], errors="coerce")
            valid = series.dropna()
            if valid.empty:
                continue

            mean_7d = _safe_mean(series.tail(7))
            mean_30d = _safe_mean(series.tail(30))
            mean_90d = _safe_mean(series.tail(90))

            wow_delta_pct: float | None = None
            if len(df) >= 14:
                prev_7d = _safe_mean(series.iloc[-14:-7])
                if mean_7d is not None and prev_7d is not None and abs(prev_7d) > 1e-9:
                    wow_delta_pct = round(((mean_7d - prev_7d) / abs(prev_7d)) * 100.0, 2)

            slope: float | None = None
            r_sq: float | None = None
            direction = "stable"
            if len(valid) >= 7 and valid.nunique() > 1:
                x = np.arange(len(valid), dtype=float)
                reg = stats.linregress(x, valid.to_numpy(dtype=float))
                slope = round(float(reg.slope), 4)
                r_sq = round(float(reg.rvalue**2), 4)
                direction = _classify_direction(float(reg.slope), float(valid.std()))

            weekday_mean = _safe_mean(series[~df["is_weekend"]])
            weekend_mean = _safe_mean(series[df["is_weekend"]])

            trends.append(
                MetricTrend(
                    domain=domain,
                    metric_name=metric,
                    sample_size=int(len(valid)),
                    mean_7d=mean_7d,
                    mean_30d=mean_30d,
                    mean_90d=mean_90d,
                    wow_delta_pct=wow_delta_pct,
                    linear_slope_per_day=slope,
                    r_squared=r_sq,
                    direction=direction,
                    weekday_mean=weekday_mean,
                    weekend_mean=weekend_mean,
                )
            )

        correlation_pairs = (
            ("sleep_hours", "energy_score", 0),
            ("sleep_hours", "deep_work_hours", 0),
            ("sleep_hours", "discretionary_spend", 1),
            ("meeting_hours", "deep_work_hours", 0),
            ("exercise_minutes", "sleep_quality_score", 0),
        )
        correlations: list[CorrelationItem] = []
        for src, tgt, lag in correlation_pairs:
            item = _compute_lagged_correlation(df, src, tgt, lag)
            if item is not None:
                correlations.append(item)

        return TrendAnalysisResult(
            start_date=df["snapshot_date"].iloc[0].strftime("%Y-%m-%d"),
            end_date=df["snapshot_date"].iloc[-1].strftime("%Y-%m-%d"),
            total_days=len(df),
            trends=trends,
            correlations=correlations,
        )
