-- Create normalized CORE views for the 3 domains without raw free-text PII columns.
-- PII fields (merchant_raw, account_alias, notes_raw, title_raw) remain isolated in RAW.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;
USE SCHEMA CORE;

CREATE OR REPLACE VIEW CORE_TRANSACTIONS AS
SELECT
    tx_id,
    tx_timestamp,
    CAST(tx_timestamp AS DATE) AS tx_date,
    amount,
    UPPER(TRIM(currency)) AS currency,
    LOWER(TRIM(direction)) AS direction,
    UPPER(TRIM(category_raw)) AS category,
    COALESCE(is_discretionary, TRUE) AS is_discretionary,
    source_system,
    ingested_at
FROM LUP_AI_DB.RAW.RAW_FINANCE_TX
QUALIFY ROW_NUMBER() OVER (PARTITION BY tx_id ORDER BY ingested_at DESC) = 1;

CREATE OR REPLACE VIEW CORE_HABITS_SLEEP AS
SELECT
    record_id,
    log_date,
    sleep_start_ts,
    sleep_end_ts,
    sleep_hours,
    sleep_quality_score,
    COALESCE(exercise_minutes, 0) AS exercise_minutes,
    COALESCE(habit_completed_count, 0) AS habit_completed_count,
    COALESCE(habit_total_count, 0) AS habit_total_count,
    CASE
        WHEN COALESCE(habit_total_count, 0) > 0
            THEN ROUND(habit_completed_count / habit_total_count, 4)
        ELSE 0.0
    END AS habit_completion_rate,
    energy_score,
    source_system,
    ingested_at
FROM LUP_AI_DB.RAW.RAW_HABITS_SLEEP
QUALIFY ROW_NUMBER() OVER (PARTITION BY log_date ORDER BY ingested_at DESC) = 1;

CREATE OR REPLACE VIEW CORE_TIME_BLOCKS AS
SELECT
    event_id,
    start_ts,
    end_ts,
    CAST(start_ts AS DATE) AS event_date,
    duration_minutes,
    ROUND(duration_minutes / 60.0, 2) AS duration_hours,
    UPPER(TRIM(domain_tag)) AS domain_tag,
    UPPER(TRIM(block_type)) AS block_type,
    LOWER(TRIM(COALESCE(priority, 'medium'))) AS priority,
    COALESCE(completed_flag, TRUE) AS completed_flag,
    source_system,
    ingested_at
FROM LUP_AI_DB.RAW.RAW_TIME_CALENDAR
QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ingested_at DESC) = 1;

