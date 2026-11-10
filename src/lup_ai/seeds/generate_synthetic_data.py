"""Deterministic multi-domain synthetic dataset generator for LUP AI."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from lup_ai.data.local_store import LocalStore


@dataclass(frozen=True)
class DatasetSummary:
    """Counts and output file paths produced by the synthetic generator."""

    days: int
    finance_rows: int
    habits_rows: int
    time_rows: int
    output_dir: Path


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_default_goals(reference_end: date) -> list[dict[str, Any]]:
    return [
        {
            "goal_id": "goal_savings_01",
            "domain": "finance",
            "title": "Ahorro semanal constante",
            "metric_name": "weekly_allocated_savings",
            "target_value": 350000.0,
            "unit": "COP",
            "comparison_operator": ">=",
            "deadline": (reference_end + timedelta(days=120)).isoformat(),
            "priority": 1,
            "status": "active",
        },
        {
            "goal_id": "goal_discretionary_02",
            "domain": "finance",
            "title": "Tope semanal gasto discrecional",
            "metric_name": "weekly_discretionary_spend",
            "target_value": 450000.0,
            "unit": "COP",
            "comparison_operator": "<=",
            "deadline": (reference_end + timedelta(days=90)).isoformat(),
            "priority": 2,
            "status": "active",
        },
        {
            "goal_id": "goal_sleep_03",
            "domain": "habits_sleep",
            "title": "Promedio minimo de sueno",
            "metric_name": "avg_sleep_hours",
            "target_value": 7.5,
            "unit": "hours",
            "comparison_operator": ">=",
            "deadline": (reference_end + timedelta(days=60)).isoformat(),
            "priority": 1,
            "status": "active",
        },
        {
            "goal_id": "goal_deepwork_04",
            "domain": "time_calendar",
            "title": "Bloques semanales de foco profundo",
            "metric_name": "weekly_deep_work_hours",
            "target_value": 20.0,
            "unit": "hours",
            "comparison_operator": ">=",
            "deadline": (reference_end + timedelta(days=90)).isoformat(),
            "priority": 2,
            "status": "active",
        },
    ]


def _build_default_preferences() -> list[dict[str, Any]]:
    return [
        {
            "pref_id": "pref_sleep_floor",
            "domain": "habits_sleep",
            "key_name": "min_sleep_hours",
            "value_json": json.dumps({"min_hours": 6.5}),
            "is_hard_constraint": True,
        },
        {
            "pref_id": "pref_privacy_mode",
            "domain": "system",
            "key_name": "privacy_mode",
            "value_json": json.dumps({"mode": "masked_cloud"}),
            "is_hard_constraint": True,
        },
        {
            "pref_id": "pref_feedback_style",
            "domain": "system",
            "key_name": "communication_style",
            "value_json": json.dumps({"tone": "concise_evidence_first", "currency": "COP"}),
            "is_hard_constraint": False,
        },
    ]


def _generate_day_habits(
    current_date: date,
    day_index: int,
    total_days: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    is_weekend = current_date.weekday() >= 5
    base_sleep = 7.8 if is_weekend else 7.1
    sleep_hours = float(np.clip(rng.normal(base_sleep, 0.55), 4.5, 9.5))

    # Inject a deterministic short-sleep streak anomaly near the last third of the series.
    anomaly_start = max(10, total_days - 40)
    if anomaly_start <= day_index < anomaly_start + 4:
        sleep_hours = float(np.round(rng.uniform(4.8, 5.4), 2))
    else:
        sleep_hours = round(sleep_hours, 2)

    sleep_end_hour = 8 if is_weekend else 6
    sleep_end_minute = int(rng.integers(0, 45))
    sleep_end_ts = datetime.combine(current_date, time(sleep_end_hour, sleep_end_minute))
    sleep_start_ts = sleep_end_ts - timedelta(hours=sleep_hours)

    sleep_quality = float(np.clip(55.0 + (sleep_hours - 5.5) * 14.0 + rng.normal(0, 5.0), 30, 98))
    exercise_minutes = int(rng.choice([0, 30, 45, 60], p=[0.35, 0.30, 0.25, 0.10]))
    habit_total = 5
    exercise_bonus = 0.12 if exercise_minutes else 0.0
    habit_prob = float(np.clip(0.45 + (sleep_hours - 6.0) * 0.12 + exercise_bonus, 0.2, 0.95))
    habit_completed = int(rng.binomial(habit_total, habit_prob))
    energy_raw = (
        4.2 + (sleep_hours - 6.0) * 1.1 + (0.6 if exercise_minutes else 0.0) + rng.normal(0, 0.5)
    )
    energy_score = float(np.clip(energy_raw, 1.0, 10.0))

    notes_raw = (
        f"Registro personal de usuario.demo@example.local; "
        f"energia={round(energy_score, 1)} tras rutina diaria."
    )

    return {
        "record_id": f"hab_{current_date.isoformat()}",
        "log_date": current_date.isoformat(),
        "sleep_start_ts": sleep_start_ts.strftime("%Y-%m-%d %H:%M:%S"),
        "sleep_end_ts": sleep_end_ts.strftime("%Y-%m-%d %H:%M:%S"),
        "sleep_hours": sleep_hours,
        "sleep_quality_score": round(sleep_quality, 2),
        "exercise_minutes": exercise_minutes,
        "habit_completed_count": habit_completed,
        "habit_total_count": habit_total,
        "energy_score": round(energy_score, 2),
        "notes_raw": notes_raw,
        "source_system": "synthetic",
    }


def _generate_day_finance(
    current_date: date,
    day_index: int,
    total_days: int,
    sleep_hours: float,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tx_counter = 1
    is_weekend = current_date.weekday() >= 5

    def add_tx(
        hour: int,
        amount: float,
        direction: str,
        category: str,
        merchant: str,
        account: str,
        discretionary: bool,
        notes: str,
    ) -> None:
        nonlocal tx_counter
        ts = datetime.combine(current_date, time(hour, int(rng.integers(0, 59))))
        rows.append(
            {
                "tx_id": f"tx_{current_date.strftime('%Y%m%d')}_{tx_counter:02d}",
                "tx_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "amount": round(float(amount), 2),
                "currency": "COP",
                "direction": direction,
                "category_raw": category,
                "merchant_raw": merchant,
                "account_alias": account,
                "is_discretionary": discretionary,
                "notes_raw": notes,
                "source_system": "synthetic",
            }
        )
        tx_counter += 1

    # Biweekly income on days 1 and 15.
    if current_date.day in (1, 15):
        add_tx(
            9,
            3200000.0,
            "income",
            "SALARY",
            "Empleador Demo SAS",
            "Cuenta Nomina ****4821",
            False,
            "Pago quincenal de nomina",
        )
        add_tx(
            10,
            350000.0,
            "saving",
            "EMERGENCY_FUND",
            "Bolsillo Ahorro Local",
            "Cuenta Ahorros ****9012",
            False,
            "Transferencia quincenal programada",
        )

    # Monthly fixed expenses on day 5.
    if current_date.day == 5:
        add_tx(
            8,
            1500000.0,
            "expense",
            "HOUSING",
            "Inmobiliaria Norte - Carlos Ruiz",
            "Cuenta Nomina ****4821",
            False,
            "Canon mensual apartamento",
        )
        add_tx(
            11,
            240000.0,
            "expense",
            "UTILITIES",
            "Energia y Conectividad Ciudad",
            "Cuenta Nomina ****4821",
            False,
            "Servicios publicos e internet",
        )

    # Daily essentials.
    if rng.random() < 0.75:
        add_tx(
            13,
            float(rng.uniform(22000, 42000)),
            "expense",
            "GROCERIES",
            "Supermercado Central #12",
            "Tarjeta Debito ****3310",
            False,
            "Compra de alimentos diarios",
        )

    # Cross-domain link: short sleep raises probability of impulse food delivery.
    delivery_prob = 0.55 if sleep_hours < 6.0 else (0.38 if is_weekend else 0.18)
    if rng.random() < delivery_prob:
        add_tx(
            20,
            float(rng.uniform(35000, 75000)),
            "expense",
            "DELIVERY",
            "App Domicilios - Repartidor Ana Diaz",
            "Tarjeta Credito ****7744",
            True,
            "Pedido nocturno a domicilio",
        )

    if is_weekend and rng.random() < 0.70:
        add_tx(
            19,
            float(rng.uniform(65000, 145000)),
            "expense",
            "ENTERTAINMENT",
            "Restaurante Mirador",
            "Tarjeta Credito ****7744",
            True,
            "Salida fin de semana con amigos",
        )

    # Inject deterministic high-spend anomalies for Phase 2 anomaly tests.
    if day_index == max(15, total_days - 25):
        add_tx(
            16,
            680000.0,
            "expense",
            "SHOPPING",
            "Tienda Tecnologia Centro",
            "Tarjeta Credito ****7744",
            True,
            "Compra impulsiva de alto monto fuera de linea base",
        )
    if day_index == max(20, total_days - 10):
        add_tx(
            21,
            320000.0,
            "expense",
            "DELIVERY",
            "App Domicilios - Pedido Grupal",
            "Tarjeta Credito ****7744",
            True,
            "Pico anomalo en domicilios",
        )

    return rows


def _generate_day_time_blocks(
    current_date: date,
    sleep_hours: float,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    is_weekend = current_date.weekday() >= 5
    seq = 1

    def add_block(
        start_hour: int,
        duration_min: int,
        domain_tag: str,
        block_type: str,
        title: str,
        priority: str,
        completed: bool,
    ) -> None:
        nonlocal seq
        start_ts = datetime.combine(current_date, time(start_hour, 0))
        end_ts = start_ts + timedelta(minutes=duration_min)
        rows.append(
            {
                "event_id": f"evt_{current_date.strftime('%Y%m%d')}_{seq:02d}",
                "start_ts": start_ts.strftime("%Y-%m-%d %H:%M:%S"),
                "end_ts": end_ts.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_minutes": duration_min,
                "domain_tag": domain_tag,
                "block_type": block_type,
                "title_raw": title,
                "priority": priority,
                "completed_flag": completed,
                "source_system": "synthetic",
            }
        )
        seq += 1

    if not is_weekend:
        deep_minutes = int(rng.choice([120, 150, 180, 240], p=[0.20, 0.30, 0.35, 0.15]))
        if sleep_hours < 6.0:
            deep_minutes = max(60, deep_minutes - 60)
        add_block(
            8,
            deep_minutes,
            "work",
            "DEEP_WORK",
            "Sesion arquitectura LUP con Juan Perez",
            "high",
            bool(rng.random() < (0.90 if sleep_hours >= 6.5 else 0.65)),
        )
        meeting_minutes = int(rng.choice([60, 90, 120, 150], p=[0.25, 0.35, 0.25, 0.15]))
        add_block(
            14,
            meeting_minutes,
            "work",
            "MEETING",
            "Sincronizacion equipo producto",
            "medium",
            True,
        )
        add_block(
            18,
            60,
            "personal",
            "LEISURE",
            "Lectura y desconexion vespertina",
            "low",
            True,
        )
    else:
        add_block(
            10,
            120,
            "personal",
            "LEISURE",
            "Caminata y tiempo familiar",
            "medium",
            True,
        )
        if rng.random() < 0.6:
            add_block(
                16,
                90,
                "learning",
                "DEEP_WORK",
                "Estudio personal datos y Python",
                "medium",
                True,
            )

    return rows


def generate_dataset(
    output_dir: Path | str,
    days: int = 180,
    seed: int = 42,
    end_date: date | None = None,
    sqlite_path: Path | str | None = None,
) -> DatasetSummary:
    """Generate correlated synthetic datasets for Finance, Habits/Sleep, and Time/Calendar."""
    if days < 14:
        raise ValueError("days must be at least 14 to support rolling baselines.")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    reference_end = end_date or date(2026, 11, 10)
    start_date = reference_end - timedelta(days=days - 1)

    finance_rows: list[dict[str, Any]] = []
    habits_rows: list[dict[str, Any]] = []
    time_rows: list[dict[str, Any]] = []

    for idx in range(days):
        current_date = start_date + timedelta(days=idx)
        habit_row = _generate_day_habits(current_date, idx, days, rng)
        habits_rows.append(habit_row)

        finance_rows.extend(
            _generate_day_finance(current_date, idx, days, habit_row["sleep_hours"], rng)
        )
        time_rows.extend(_generate_day_time_blocks(current_date, habit_row["sleep_hours"], rng))

    _write_csv(
        out_path / "finance_tx.csv",
        [
            "tx_id",
            "tx_timestamp",
            "amount",
            "currency",
            "direction",
            "category_raw",
            "merchant_raw",
            "account_alias",
            "is_discretionary",
            "notes_raw",
            "source_system",
        ],
        finance_rows,
    )
    _write_csv(
        out_path / "habits_sleep.csv",
        [
            "record_id",
            "log_date",
            "sleep_start_ts",
            "sleep_end_ts",
            "sleep_hours",
            "sleep_quality_score",
            "exercise_minutes",
            "habit_completed_count",
            "habit_total_count",
            "energy_score",
            "notes_raw",
            "source_system",
        ],
        habits_rows,
    )
    _write_csv(
        out_path / "time_calendar.csv",
        [
            "event_id",
            "start_ts",
            "end_ts",
            "duration_minutes",
            "domain_tag",
            "block_type",
            "title_raw",
            "priority",
            "completed_flag",
            "source_system",
        ],
        time_rows,
    )

    goals = _build_default_goals(reference_end)
    preferences = _build_default_preferences()
    (out_path / "goals.json").write_text(json.dumps(goals, indent=2), encoding="utf-8")
    (out_path / "preferences.json").write_text(json.dumps(preferences, indent=2), encoding="utf-8")

    if sqlite_path is not None:
        store = LocalStore(sqlite_path)
        for goal in goals:
            store.upsert_goal(goal)
        for pref in preferences:
            store.upsert_preference(pref)

    return DatasetSummary(
        days=days,
        finance_rows=len(finance_rows),
        habits_rows=len(habits_rows),
        time_rows=len(time_rows),
        output_dir=out_path,
    )


def main() -> None:
    """CLI entry point to generate LUP AI Phase 0 synthetic data."""
    parser = argparse.ArgumentParser(description="Generate synthetic multi-domain data for LUP AI.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sqlite-path", type=Path, default=None)
    args = parser.parse_args()

    summary = generate_dataset(
        output_dir=args.output_dir,
        days=args.days,
        seed=args.seed,
        sqlite_path=args.sqlite_path,
    )
    print(
        f"Generated {summary.days} days in {summary.output_dir}: "
        f"finance={summary.finance_rows}, habits={summary.habits_rows}, time={summary.time_rows}"
    )


if __name__ == "__main__":
    main()
