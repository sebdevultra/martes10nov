"""Unit tests for multi-domain synthetic dataset generator."""

import csv
import json
from pathlib import Path

from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_generate_dataset_creates_all_three_domains_and_seeds_sqlite(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    db_path = tmp_path / "lup.db"
    summary = generate_dataset(
        output_dir=out_dir,
        days=30,
        seed=42,
        sqlite_path=db_path,
    )

    assert summary.days == 30
    assert summary.habits_rows == 30
    assert summary.finance_rows > 30
    assert summary.time_rows > 30

    for filename in (
        "finance_tx.csv",
        "habits_sleep.csv",
        "time_calendar.csv",
        "goals.json",
        "preferences.json",
    ):
        assert (out_dir / filename).exists()

    with (out_dir / "finance_tx.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        assert {"tx_id", "amount", "category_raw", "is_discretionary"} <= set(rows[0].keys())

    goals = json.loads((out_dir / "goals.json").read_text(encoding="utf-8"))
    assert len(goals) == 4

    store = LocalStore(db_path)
    assert len(store.list_active_goals()) == 4
    assert len(store.list_preferences()) == 3
