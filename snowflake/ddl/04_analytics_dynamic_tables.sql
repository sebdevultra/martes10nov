-- Create ANALYTICS context tables and cross-domain Dynamic Tables.
-- REFRESH_MODE = AUTO ensures reliable refreshes across Snowflake editions.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;
USE SCHEMA ANALYTICS;

CREATE TABLE IF NOT EXISTS DIM_GOALS (
    goal_id VARCHAR(64) NOT NULL,
    domain VARCHAR(32) NOT NULL,
    title VARCHAR(160) NOT NULL,
    metric_name VARCHAR(64) NOT NULL,
    target_value NUMBER(14, 2) NOT NULL,
    unit VARCHAR(32) NOT NULL,
    comparison_operator VARCHAR(8) DEFAULT '>=',
    deadline DATE,
    priority NUMBER(2, 0) DEFAULT 1,
    status VARCHAR(16) DEFAULT 'active',
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    updated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_dim_goals PRIMARY KEY (goal_id)
);

CREATE TABLE IF NOT EXISTS DIM_PREFERENCES (
    pref_id VARCHAR(64) NOT NULL,
    domain VARCHAR(32) NOT NULL,
    key_name VARCHAR(64) NOT NULL,
    value_json VARCHAR(1024) NOT NULL,
    is_hard_constraint BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_dim_preferences PRIMARY KEY (pref_id)
);

CREATE TABLE IF NOT EXISTS FCT_ANOMALIES (
    anomaly_id VARCHAR(64) NOT NULL,
    detected_date DATE NOT NULL,
    domain VARCHAR(32) NOT NULL,
    metric_name VARCHAR(64) NOT NULL,
    expected_value NUMBER(14, 4),
    actual_value NUMBER(14, 4),
    z_score NUMBER(8, 4),
    severity VARCHAR(16) NOT NULL,
    status VARCHAR(16) DEFAULT 'open',
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_fct_anomalies PRIMARY KEY (anomaly_id)
);

CREATE TABLE IF NOT EXISTS FCT_DECISIONS_LOG (
    decision_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    domain VARCHAR(32) NOT NULL,
    prompt_summary VARCHAR(512),
    recommendation_json VARIANT NOT NULL,
    evidence_json VARIANT NOT NULL,
    model_used VARCHAR(64) NOT NULL,
    privacy_mode VARCHAR(32) DEFAULT 'masked_cloud',
    user_action VARCHAR(24) DEFAULT 'pending',
    CONSTRAINT pk_fct_decisions_log PRIMARY KEY (decision_id)
);

CREATE TABLE IF NOT EXISTS FCT_DECISION_OUTCOMES (
    outcome_id VARCHAR(64) NOT NULL,
    decision_id VARCHAR(64) NOT NULL,
    evaluated_at DATE NOT NULL,
    delta_impact NUMBER(14, 4),
    outcome_status VARCHAR(24) NOT NULL,
    user_feedback VARCHAR(512),
    CONSTRAINT pk_fct_decision_outcomes PRIMARY KEY (outcome_id)
);

CREATE OR REPLACE DYNAMIC TABLE FCT_DAILY_SNAPSHOT
    TARGET_LAG = '12 hours'
    WAREHOUSE = LUP_WH_XS
    REFRESH_MODE = AUTO
AS
WITH all_dates AS (
    SELECT tx_date AS snapshot_date FROM LUP_AI_DB.CORE.CORE_TRANSACTIONS
    UNION
    SELECT log_date AS snapshot_date FROM LUP_AI_DB.CORE.CORE_HABITS_SLEEP
    UNION
    SELECT event_date AS snapshot_date FROM LUP_AI_DB.CORE.CORE_TIME_BLOCKS
),
finance_daily AS (
    SELECT
        tx_date AS snapshot_date,
        COALESCE(SUM(CASE WHEN direction = 'income' THEN amount ELSE 0 END), 0) AS total_income,
        COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount ELSE 0 END), 0) AS total_spend,
        COALESCE(SUM(CASE WHEN direction = 'expense' AND is_discretionary THEN amount ELSE 0 END), 0) AS discretionary_spend,
        COALESCE(SUM(CASE WHEN direction = 'saving' THEN amount ELSE 0 END), 0) AS allocated_savings
    FROM LUP_AI_DB.CORE.CORE_TRANSACTIONS
    GROUP BY tx_date
),
habits_daily AS (
    SELECT
        log_date AS snapshot_date,
        AVG(sleep_hours) AS sleep_hours,
        AVG(sleep_quality_score) AS sleep_quality_score,
        SUM(exercise_minutes) AS exercise_minutes,
        AVG(habit_completion_rate) AS habit_completion_rate,
        AVG(energy_score) AS energy_score
    FROM LUP_AI_DB.CORE.CORE_HABITS_SLEEP
    GROUP BY log_date
),
time_daily AS (
    SELECT
        event_date AS snapshot_date,
        COALESCE(SUM(CASE WHEN block_type = 'DEEP_WORK' AND completed_flag THEN duration_hours ELSE 0 END), 0) AS deep_work_hours,
        COALESCE(SUM(CASE WHEN block_type = 'MEETING' AND completed_flag THEN duration_hours ELSE 0 END), 0) AS meeting_hours,
        COALESCE(SUM(CASE WHEN block_type = 'LEISURE' AND completed_flag THEN duration_hours ELSE 0 END), 0) AS leisure_hours,
        COALESCE(SUM(CASE WHEN completed_flag THEN duration_hours ELSE 0 END), 0) AS total_scheduled_hours
    FROM LUP_AI_DB.CORE.CORE_TIME_BLOCKS
    GROUP BY event_date
)
SELECT
    d.snapshot_date,
    DATE_TRUNC('WEEK', d.snapshot_date) AS week_start,
    DAYNAME(d.snapshot_date) AS day_of_week,
    COALESCE(f.total_income, 0) AS total_income,
    COALESCE(f.total_spend, 0) AS total_spend,
    COALESCE(f.discretionary_spend, 0) AS discretionary_spend,
    COALESCE(f.total_income, 0) - COALESCE(f.total_spend, 0) AS net_cashflow,
    COALESCE(f.allocated_savings, 0) AS allocated_savings,
    h.sleep_hours,
    h.sleep_quality_score,
    COALESCE(h.exercise_minutes, 0) AS exercise_minutes,
    h.habit_completion_rate,
    h.energy_score,
    COALESCE(t.deep_work_hours, 0) AS deep_work_hours,
    COALESCE(t.meeting_hours, 0) AS meeting_hours,
    COALESCE(t.leisure_hours, 0) AS leisure_hours,
    COALESCE(t.total_scheduled_hours, 0) AS total_scheduled_hours
FROM all_dates d
LEFT JOIN finance_daily f ON d.snapshot_date = f.snapshot_date
LEFT JOIN habits_daily h ON d.snapshot_date = h.snapshot_date
LEFT JOIN time_daily t ON d.snapshot_date = t.snapshot_date;

CREATE OR REPLACE DYNAMIC TABLE FCT_WEEKLY_TRENDS
    TARGET_LAG = '12 hours'
    WAREHOUSE = LUP_WH_XS
    REFRESH_MODE = AUTO
AS
SELECT
    week_start,
    COUNT(*) AS active_days,
    SUM(total_income) AS weekly_income,
    SUM(total_spend) AS weekly_spend,
    SUM(discretionary_spend) AS weekly_discretionary_spend,
    SUM(net_cashflow) AS weekly_net_cashflow,
    SUM(allocated_savings) AS weekly_allocated_savings,
    ROUND(AVG(sleep_hours), 2) AS avg_sleep_hours,
    ROUND(AVG(sleep_quality_score), 2) AS avg_sleep_quality_score,
    SUM(exercise_minutes) AS weekly_exercise_minutes,
    ROUND(AVG(habit_completion_rate), 4) AS avg_habit_completion_rate,
    ROUND(AVG(energy_score), 2) AS avg_energy_score,
    SUM(deep_work_hours) AS weekly_deep_work_hours,
    SUM(meeting_hours) AS weekly_meeting_hours,
    SUM(leisure_hours) AS weekly_leisure_hours
FROM LUP_AI_DB.ANALYTICS.FCT_DAILY_SNAPSHOT
GROUP BY week_start;

