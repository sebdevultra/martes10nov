"""Local synthetic CSV repository mirroring Snowflake SEMANTIC views for offline development."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import sqlglot

from lup_ai.data.sql_guard import validate_and_prepare_sql


def _week_start(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series)
    return (dt - pd.to_timedelta(dt.dt.day_of_week, unit="D")).dt.strftime("%Y-%m-%d")


class LocalCsvAnalyticsRepository:
    """Mirror Snowflake SEMANTIC views locally over generated CSV/JSON files."""

    def __init__(self, data_dir: Path | str = Path("data/generated")) -> None:
        self.data_dir = Path(data_dir)
        self._views_cache: dict[str, pd.DataFrame] | None = None

    @property
    def backend_name(self) -> str:
        return "local_synthetic_csv"

    def is_available(self) -> bool:
        """Return True when synthetic CSV files exist in data_dir."""
        required = ("finance_tx.csv", "habits_sleep.csv", "time_calendar.csv")
        return all((self.data_dir / fname).exists() for fname in required)

    def _build_views(self) -> dict[str, pd.DataFrame]:
        if self._views_cache is not None:
            return self._views_cache
        if not self.is_available():
            raise FileNotFoundError(
                f"Synthetic dataset not found in {self.data_dir}. Run lup-seed first."
            )

        fin_raw = pd.read_csv(self.data_dir / "finance_tx.csv")
        hab_raw = pd.read_csv(self.data_dir / "habits_sleep.csv")
        time_raw = pd.read_csv(self.data_dir / "time_calendar.csv")

        # Mirror CORE normalization without PII columns.
        fin_raw["tx_date"] = pd.to_datetime(fin_raw["tx_timestamp"]).dt.strftime("%Y-%m-%d")
        fin_raw["week_start"] = _week_start(fin_raw["tx_date"])
        fin_raw["month_start"] = pd.to_datetime(fin_raw["tx_date"]).dt.strftime("%Y-%m-01")
        fin_raw["direction"] = fin_raw["direction"].astype(str).str.strip().str.lower()
        fin_raw["category"] = fin_raw["category_raw"].astype(str).str.strip().str.upper()
        fin_raw["is_discretionary"] = fin_raw["is_discretionary"].astype(bool)

        vw_finance = (
            fin_raw.groupby(
                [
                    "tx_date",
                    "week_start",
                    "month_start",
                    "currency",
                    "direction",
                    "category",
                    "is_discretionary",
                ],
                as_index=False,
            )
            .agg(
                tx_count=("tx_id", "count"),
                total_amount=("amount", "sum"),
                avg_amount=("amount", "mean"),
            )
            .assign(avg_amount=lambda df: df["avg_amount"].round(2))
        )

        hab_raw["log_date"] = pd.to_datetime(hab_raw["log_date"]).dt.strftime("%Y-%m-%d")
        hab_raw["week_start"] = _week_start(hab_raw["log_date"])
        hab_raw["habit_completion_rate"] = (
            (hab_raw["habit_completed_count"] / hab_raw["habit_total_count"].replace(0, pd.NA))
            .fillna(0.0)
            .round(4)
        )

        vw_habits = hab_raw[
            [
                "log_date",
                "week_start",
                "sleep_hours",
                "sleep_quality_score",
                "exercise_minutes",
                "habit_completed_count",
                "habit_total_count",
                "habit_completion_rate",
                "energy_score",
            ]
        ].copy()

        time_raw["event_date"] = pd.to_datetime(time_raw["start_ts"]).dt.strftime("%Y-%m-%d")
        time_raw["week_start"] = _week_start(time_raw["event_date"])
        time_raw["domain_tag"] = time_raw["domain_tag"].astype(str).str.strip().str.upper()
        time_raw["block_type"] = time_raw["block_type"].astype(str).str.strip().str.upper()
        time_raw["priority"] = time_raw["priority"].astype(str).str.strip().str.lower()
        time_raw["completed_flag"] = time_raw["completed_flag"].astype(bool)
        time_raw["duration_hours"] = (time_raw["duration_minutes"] / 60.0).round(2)

        vw_time = time_raw.groupby(
            [
                "event_date",
                "week_start",
                "domain_tag",
                "block_type",
                "priority",
                "completed_flag",
            ],
            as_index=False,
        ).agg(
            block_count=("event_id", "count"),
            total_minutes=("duration_minutes", "sum"),
            total_hours=("duration_hours", "sum"),
        )

        all_dates = sorted(
            set(vw_finance["tx_date"]) | set(vw_habits["log_date"]) | set(vw_time["event_date"])
        )
        spine = pd.DataFrame({"snapshot_date": all_dates})
        spine["week_start"] = _week_start(spine["snapshot_date"])
        spine["day_of_week"] = pd.to_datetime(spine["snapshot_date"]).dt.strftime("%a")

        fin_daily = pd.DataFrame({"snapshot_date": all_dates})
        income_map = fin_raw[fin_raw["direction"] == "income"].groupby("tx_date")["amount"].sum()
        spend_map = fin_raw[fin_raw["direction"] == "expense"].groupby("tx_date")["amount"].sum()
        disc_map = (
            fin_raw[(fin_raw["direction"] == "expense") & (fin_raw["is_discretionary"])]
            .groupby("tx_date")["amount"]
            .sum()
        )
        save_map = fin_raw[fin_raw["direction"] == "saving"].groupby("tx_date")["amount"].sum()
        fin_daily["total_income"] = fin_daily["snapshot_date"].map(income_map).fillna(0.0)
        fin_daily["total_spend"] = fin_daily["snapshot_date"].map(spend_map).fillna(0.0)
        fin_daily["discretionary_spend"] = fin_daily["snapshot_date"].map(disc_map).fillna(0.0)
        fin_daily["net_cashflow"] = fin_daily["total_income"] - fin_daily["total_spend"]
        fin_daily["allocated_savings"] = fin_daily["snapshot_date"].map(save_map).fillna(0.0)

        hab_daily = vw_habits.rename(columns={"log_date": "snapshot_date"})[
            [
                "snapshot_date",
                "sleep_hours",
                "sleep_quality_score",
                "exercise_minutes",
                "habit_completion_rate",
                "energy_score",
            ]
        ]

        comp_time = time_raw[time_raw["completed_flag"]]
        dw_map = (
            comp_time[comp_time["block_type"] == "DEEP_WORK"]
            .groupby("event_date")["duration_hours"]
            .sum()
        )
        mt_map = (
            comp_time[comp_time["block_type"] == "MEETING"]
            .groupby("event_date")["duration_hours"]
            .sum()
        )
        le_map = (
            comp_time[comp_time["block_type"] == "LEISURE"]
            .groupby("event_date")["duration_hours"]
            .sum()
        )
        tot_map = comp_time.groupby("event_date")["duration_hours"].sum()

        time_daily = pd.DataFrame({"snapshot_date": all_dates})
        time_daily["deep_work_hours"] = time_daily["snapshot_date"].map(dw_map).fillna(0.0)
        time_daily["meeting_hours"] = time_daily["snapshot_date"].map(mt_map).fillna(0.0)
        time_daily["leisure_hours"] = time_daily["snapshot_date"].map(le_map).fillna(0.0)
        time_daily["total_scheduled_hours"] = time_daily["snapshot_date"].map(tot_map).fillna(0.0)

        vw_cross = (
            spine.merge(fin_daily, on="snapshot_date", how="left")
            .merge(hab_daily, on="snapshot_date", how="left")
            .merge(time_daily, on="snapshot_date", how="left")
        )

        weekly = vw_cross.groupby("week_start", as_index=False).agg(
            weekly_allocated_savings=("allocated_savings", "sum"),
            weekly_discretionary_spend=("discretionary_spend", "sum"),
            avg_sleep_hours=("sleep_hours", "mean"),
            weekly_deep_work_hours=("deep_work_hours", "sum"),
            avg_habit_completion_rate=("habit_completion_rate", "mean"),
        )
        latest_week_row = weekly.sort_values("week_start").iloc[-1] if not weekly.empty else None

        goals_path = self.data_dir / "goals.json"
        goals_list = (
            json.loads(goals_path.read_text(encoding="utf-8")) if goals_path.exists() else []
        )
        goal_rows: list[dict[str, Any]] = []
        for g in goals_list:
            if g.get("status", "active") != "active":
                continue
            metric = g["metric_name"]
            observed = None
            latest_week = None
            if latest_week_row is not None and metric in latest_week_row:
                latest_week = str(latest_week_row["week_start"])
                observed = round(float(latest_week_row[metric]), 2)
            goal_rows.append(
                {
                    **g,
                    "latest_week": latest_week,
                    "latest_observed_value": observed,
                }
            )
        vw_goals = pd.DataFrame(goal_rows)

        self._views_cache = {
            "VW_FINANCE_SUMMARY": vw_finance,
            "VW_HABITS_SLEEP_SUMMARY": vw_habits,
            "VW_TIME_CALENDAR_SUMMARY": vw_time,
            "VW_CROSS_DOMAIN_DAILY": vw_cross,
            "VW_GOAL_PROGRESS": vw_goals,
        }
        return self._views_cache

    @staticmethod
    def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty:
            return []
        clean = df.where(pd.notnull(df), None)
        return clean.to_dict(orient="records")

    def execute_semantic_sql(self, sql: str) -> list[dict[str, Any]]:
        """Validate SQL with SQL Guard and execute locally against mirrored SEMANTIC tables."""
        safe_sql = validate_and_prepare_sql(sql)
        sqlite_sql = sqlglot.transpile(safe_sql, read="snowflake", write="sqlite")[0]
        for prefix in ("LUP_AI_DB.SEMANTIC.", "SEMANTIC."):
            sqlite_sql = sqlite_sql.replace(prefix, "")
            sqlite_sql = sqlite_sql.replace(prefix.lower(), "")

        views = self._build_views()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            for view_name, df in views.items():
                df.to_sql(view_name, conn, index=False, if_exists="replace")
            rows = conn.execute(sqlite_sql).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def fetch_finance_summary(
        self,
        start_date: date,
        end_date: date,
        direction: str | None = None,
        category: str | None = None,
        discretionary_only: bool = False,
    ) -> list[dict[str, Any]]:
        df = self._build_views()["VW_FINANCE_SUMMARY"]
        mask = (df["tx_date"] >= start_date.isoformat()) & (df["tx_date"] <= end_date.isoformat())
        if direction:
            mask &= df["direction"] == direction.lower()
        if category:
            mask &= df["category"] == category.upper()
        if discretionary_only:
            mask &= df["is_discretionary"]
        return self._df_to_records(df[mask].sort_values("tx_date", ascending=False))

    def fetch_habits_sleep_summary(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        df = self._build_views()["VW_HABITS_SLEEP_SUMMARY"]
        mask = (df["log_date"] >= start_date.isoformat()) & (df["log_date"] <= end_date.isoformat())
        return self._df_to_records(df[mask].sort_values("log_date", ascending=False))

    def fetch_time_calendar_summary(
        self,
        start_date: date,
        end_date: date,
        domain_tag: str | None = None,
        block_type: str | None = None,
    ) -> list[dict[str, Any]]:
        df = self._build_views()["VW_TIME_CALENDAR_SUMMARY"]
        mask = (df["event_date"] >= start_date.isoformat()) & (
            df["event_date"] <= end_date.isoformat()
        )
        if domain_tag:
            mask &= df["domain_tag"] == domain_tag.upper()
        if block_type:
            mask &= df["block_type"] == block_type.upper()
        return self._df_to_records(df[mask].sort_values("event_date", ascending=False))

    def fetch_cross_domain_daily(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        df = self._build_views()["VW_CROSS_DOMAIN_DAILY"]
        mask = (df["snapshot_date"] >= start_date.isoformat()) & (
            df["snapshot_date"] <= end_date.isoformat()
        )
        return self._df_to_records(df[mask].sort_values("snapshot_date", ascending=False))

    def fetch_goal_progress(self) -> list[dict[str, Any]]:
        df = self._build_views()["VW_GOAL_PROGRESS"]
        if df.empty:
            return []
        return self._df_to_records(df.sort_values(["priority", "goal_id"]))
