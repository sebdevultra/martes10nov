"""Unit tests for Phase 2 deterministic engines (trends, anomalies, simulation, recommendations)."""

from datetime import date
from pathlib import Path

from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.engines.anomaly_detector import AnomalyDetector
from lup_ai.engines.recommendation_builder import RecommendationBuilder
from lup_ai.engines.simulation_engine import SimulationEngine, SimulationScenario
from lup_ai.engines.trend_analyzer import TrendAnalyzer
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_engines_pipeline_over_synthetic_dataset(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=180,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    store = LocalStore(db_path)
    daily_rows = repo.fetch_cross_domain_daily(date(2026, 5, 15), date(2026, 11, 10))
    goals = store.list_active_goals()
    prefs = store.list_preferences()

    # 1. TrendAnalyzer produces rolling metrics and non-causal correlations.
    trends = TrendAnalyzer().analyze(daily_rows)
    assert trends.total_days == 180
    assert len(trends.trends) >= 8
    assert all(c.correlation_only for c in trends.correlations)

    # 2. AnomalyDetector catches injected discretionary spikes and short-sleep streak.
    scan = AnomalyDetector().scan(daily_rows, preferences=prefs, goals=goals)
    metrics_detected = {a.metric_name for a in scan.anomalies}
    assert "discretionary_spend" in metrics_detected
    assert "sleep_hours_streak" in metrics_detected
    assert scan.opportunities

    # 3. SimulationEngine projects p25 <= p50 <= p75 and respects hard constraints.
    scenario = SimulationScenario(
        horizon_days=90,
        discretionary_spend_delta_pct=-0.25,
        sleep_hours_target=7.5,
        deep_work_delta_hours_per_week=4.0,
        iterations=300,
        seed=42,
    )
    sim = SimulationEngine().simulate(
        daily_rows=daily_rows,
        scenario=scenario,
        goals=goals,
        preferences=prefs,
    )
    sav = sim.trajectories["cumulative_savings"]
    assert sav.conservative_p25 <= sav.base_p50 <= sav.optimistic_p75
    assert sav.delta_vs_baseline_p50 > 0
    assert sim.goal_projections
    assert not any(c.severity == "blocking" for c in sim.constraint_collisions)

    # Blocking sleep constraint collision when target < 6.5h.
    bad_sim = SimulationEngine().simulate(
        daily_rows=daily_rows,
        scenario=SimulationScenario(horizon_days=30, sleep_hours_target=5.5, iterations=200),
        goals=goals,
        preferences=prefs,
    )
    assert any(c.severity == "blocking" for c in bad_sim.constraint_collisions)

    # 4. RecommendationBuilder enforces non-empty numeric evidence.
    recs_anom = RecommendationBuilder().from_anomaly_scan(scan)
    recs_sim = RecommendationBuilder().from_simulation(sim)
    assert recs_anom and all(len(r.evidence) >= 1 for r in recs_anom)
    assert recs_sim and all(len(r.evidence) >= 1 for r in recs_sim)

