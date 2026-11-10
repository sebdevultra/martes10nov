-- Create curated read-only SEMANTIC views for LUP_AGENT_RO across the 3 domains.
-- This schema is the sole query surface permitted by the SQL Guard.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;
USE SCHEMA SEMANTIC;

CREATE OR REPLACE VIEW VW_FINANCE_SUMMARY AS
SELECT
    tx_date,
    DATE_TRUNC('WEEK', tx_date) AS week_start,
    DATE_TRUNC('MONTH', tx_date) AS month_start,
    currency,
    direction,
    category,
    is_discretionary,
    COUNT(*) AS tx_count,
    SUM(amount) AS total_amount,
    ROUND(AVG(amount), 2) AS avg_amount
FROM LUP_AI_DB.CORE.CORE_TRANSACTIONS
GROUP BY
    tx_date,
    DATE_TRUNC('WEEK', tx_date),
    DATE_TRUNC('MONTH', tx_date),
    currency,
    direction,
    category,
    is_discretionary;

CREATE OR REPLACE VIEW VW_HABITS_SLEEP_SUMMARY AS
SELECT
    log_date,
    DATE_TRUNC('WEEK', log_date) AS week_start,
    sleep_hours,
    sleep_quality_score,
    exercise_minutes,
    habit_completed_count,
    habit_total_count,
    habit_completion_rate,
    energy_score
FROM LUP_AI_DB.CORE.CORE_HABITS_SLEEP;

CREATE OR REPLACE VIEW VW_TIME_CALENDAR_SUMMARY AS
SELECT
    event_date,
    DATE_TRUNC('WEEK', event_date) AS week_start,
    domain_tag,
    block_type,
    priority,
    completed_flag,
    COUNT(*) AS block_count,
    SUM(duration_minutes) AS total_minutes,
    SUM(duration_hours) AS total_hours
FROM LUP_AI_DB.CORE.CORE_TIME_BLOCKS
GROUP BY
    event_date,
    DATE_TRUNC('WEEK', event_date),
    domain_tag,
    block_type,
    priority,
    completed_flag;

CREATE OR REPLACE VIEW VW_CROSS_DOMAIN_DAILY AS
SELECT
    snapshot_date,
    week_start,
    day_of_week,
    total_income,
    total_spend,
    discretionary_spend,
    net_cashflow,
    allocated_savings,
    sleep_hours,
    sleep_quality_score,
    exercise_minutes,
    habit_completion_rate,
    energy_score,
    deep_work_hours,
    meeting_hours,
    leisure_hours,
    total_scheduled_hours
FROM LUP_AI_DB.ANALYTICS.FCT_DAILY_SNAPSHOT;

CREATE OR REPLACE VIEW VW_GOAL_PROGRESS AS
SELECT
    g.goal_id,
    g.domain,
    g.title,
    g.metric_name,
    g.target_value,
    g.unit,
    g.comparison_operator,
    g.deadline,
    g.priority,
    g.status,
    w.week_start AS latest_week,
    CASE
        WHEN g.metric_name = 'weekly_allocated_savings' THEN w.weekly_allocated_savings
        WHEN g.metric_name = 'avg_sleep_hours' THEN w.avg_sleep_hours
        WHEN g.metric_name = 'weekly_deep_work_hours' THEN w.weekly_deep_work_hours
        WHEN g.metric_name = 'weekly_discretionary_spend' THEN w.weekly_discretionary_spend
        WHEN g.metric_name = 'avg_habit_completion_rate' THEN w.avg_habit_completion_rate
        ELSE NULL
    END AS latest_observed_value
FROM LUP_AI_DB.ANALYTICS.DIM_GOALS g
LEFT JOIN LUP_AI_DB.ANALYTICS.FCT_WEEKLY_TRENDS w
    ON w.week_start = (SELECT MAX(week_start) FROM LUP_AI_DB.ANALYTICS.FCT_WEEKLY_TRENDS)
WHERE g.status = 'active';

