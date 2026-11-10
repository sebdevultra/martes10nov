"""Unit tests for DecisionTracker outcome loop and BatchIngestor buffer/flush."""

from datetime import date
from pathlib import Path

from lup_ai.config.settings import Settings
from lup_ai.data.batch_ingestor import (
    BatchIngestor,
    FinanceEventInput,
    HabitsSleepEventInput,
    TimeBlockEventInput,
)
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.engines.recommendation_builder import RecommendationEvidence, RecommendationPayload
from lup_ai.memory.decision_tracker import DecisionTracker
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_decision_tracker_outcome_loop_evaluates_improvement(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=45,
        seed=42,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    tracker = DecisionTracker(store)

    # Sleep baseline 5.2h vs actual ~7.4h in the last 7 days should evaluate as 'improved'.
    rec = RecommendationPayload(
        recommendation_id="rec_sleep_test_01",
        domain="habits_sleep",
        title="Recuperar minimo de sueno",
        action="Dormir >= 7.0h durante 7 dias.",
        expected_impact="Retorno sobre 6.5h.",
        evidence=[
            RecommendationEvidence(
                domain="habits_sleep",
                source_view="SEMANTIC.VW_CROSS_DOMAIN_DAILY",
                metric_name="sleep_hours",
                date_range="2026-10-20 a 2026-11-01",
                baseline_value=5.2,
                observed_value=7.4,
                delta_or_z="+2.2h",
            )
        ],
        goal_alignment="goal_sleep_03",
        confidence="high",
        risks_and_tradeoffs="Ninguno.",
    )

    tracker.persist_recommendations([rec], source_type="proactive_scan")
    pending = tracker.list_decisions(user_action="pending")
    assert len(pending) == 1

    tracker.record_feedback(
        decision_id="rec_sleep_test_01",
        user_action="accepted",
        user_feedback="Aplicado esta semana",
        actioned_at="2026-11-01T09:00:00+00:00",
        evaluate_after_days=7,
    )

    outcomes = tracker.evaluate_pending_outcomes(repo=repo, anchor_date=date(2026, 11, 10))
    assert len(outcomes) == 1
    assert outcomes[0].outcome_status == "improved"
    assert outcomes[0].delta_impact > 1.0

    summary = tracker.adherence_summary()
    assert summary.completed_count == 1
    assert summary.improved_count == 1
    assert summary.acceptance_rate_pct == 100.0
    assert summary.improvement_rate_pct == 100.0


def test_batch_ingestor_buffers_and_flushes_to_local_csv_mirror(tmp_path: Path) -> None:
    data_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    generate_dataset(
        output_dir=data_dir,
        days=20,
        seed=5,
        end_date=date(2026, 11, 10),
        sqlite_path=db_path,
    )

    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    ingestor = BatchIngestor(settings=settings, store=store, local_csv_repo=repo)

    before_rows = len(repo.fetch_finance_summary(date(2026, 11, 1), date(2026, 11, 10)))

    ingestor.capture_finance(
        FinanceEventInput(
            tx_timestamp="2026-11-10 12:15:00",
            amount=55000.0,
            category_raw="SPECIAL_TEST_CAT",
            direction="expense",
            is_discretionary=True,
        )
    )
    ingestor.capture_habits_sleep(
        HabitsSleepEventInput(
            log_date="2026-11-11",
            sleep_hours=8.0,
            exercise_minutes=45,
        )
    )
    ingestor.capture_time_block(
        TimeBlockEventInput(
            start_ts="2026-11-10 10:00:00",
            end_ts="2026-11-10 11:30:00",
            duration_minutes=90,
            block_type="DEEP_WORK",
        )
    )

    assert len(store.list_pending_ingest_events()) == 3
    flush_res = ingestor.flush_batch()
    assert flush_res.flushed_count == 3
    assert flush_res.target_backend == "local_synthetic_csv"
    assert store.list_pending_ingest_events() == []

    after_rows = repo.fetch_finance_summary(date(2026, 11, 1), date(2026, 11, 10))
    assert len(after_rows) == before_rows + 1
    assert any(r["category"] == "SPECIAL_TEST_CAT" for r in after_rows)
