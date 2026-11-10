-- Load generated synthetic CSV files into RAW tables and seed initial goals and preferences.
-- Run after executing DDL scripts 01 through 05 and generating CSVs via `lup-seed`.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;

-- Upload local files from data/generated/ using SnowSQL or Snowflake CLI before COPY INTO:
-- PUT file://./data/generated/finance_tx.csv @RAW.LUP_INTERNAL_STAGE/finance/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE;
-- PUT file://./data/generated/habits_sleep.csv @RAW.LUP_INTERNAL_STAGE/habits/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE;
-- PUT file://./data/generated/time_calendar.csv @RAW.LUP_INTERNAL_STAGE/time/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE;

COPY INTO LUP_AI_DB.RAW.RAW_FINANCE_TX (
    tx_id, tx_timestamp, amount, currency, direction, category_raw,
    merchant_raw, account_alias, is_discretionary, notes_raw, source_system
)
FROM @LUP_AI_DB.RAW.LUP_INTERNAL_STAGE/finance/
FILE_FORMAT = (FORMAT_NAME = LUP_AI_DB.RAW.LUP_CSV_FORMAT)
ON_ERROR = 'ABORT_STATEMENT';

COPY INTO LUP_AI_DB.RAW.RAW_HABITS_SLEEP (
    record_id, log_date, sleep_start_ts, sleep_end_ts, sleep_hours,
    sleep_quality_score, exercise_minutes, habit_completed_count,
    habit_total_count, energy_score, notes_raw, source_system
)
FROM @LUP_AI_DB.RAW.LUP_INTERNAL_STAGE/habits/
FILE_FORMAT = (FORMAT_NAME = LUP_AI_DB.RAW.LUP_CSV_FORMAT)
ON_ERROR = 'ABORT_STATEMENT';

COPY INTO LUP_AI_DB.RAW.RAW_TIME_CALENDAR (
    event_id, start_ts, end_ts, duration_minutes, domain_tag,
    block_type, title_raw, priority, completed_flag, source_system
)
FROM @LUP_AI_DB.RAW.LUP_INTERNAL_STAGE/time/
FILE_FORMAT = (FORMAT_NAME = LUP_AI_DB.RAW.LUP_CSV_FORMAT)
ON_ERROR = 'ABORT_STATEMENT';

MERGE INTO LUP_AI_DB.ANALYTICS.DIM_GOALS AS target
USING (
    SELECT 'goal_savings_01' AS goal_id, 'finance' AS domain, 'Ahorro semanal constante' AS title,
           'weekly_allocated_savings' AS metric_name, 350000.00 AS target_value, 'COP' AS unit,
           '>=' AS comparison_operator, DATEADD('day', 120, CURRENT_DATE()) AS deadline, 1 AS priority, 'active' AS status
    UNION ALL
    SELECT 'goal_discretionary_02', 'finance', 'Tope semanal gasto discrecional',
           'weekly_discretionary_spend', 450000.00, 'COP', '<=', DATEADD('day', 90, CURRENT_DATE()), 2, 'active'
    UNION ALL
    SELECT 'goal_sleep_03', 'habits_sleep', 'Promedio minimo de sueno',
           'avg_sleep_hours', 7.50, 'hours', '>=', DATEADD('day', 60, CURRENT_DATE()), 1, 'active'
    UNION ALL
    SELECT 'goal_deepwork_04', 'time_calendar', 'Bloques semanales de foco profundo',
           'weekly_deep_work_hours', 20.00, 'hours', '>=', DATEADD('day', 90, CURRENT_DATE()), 2, 'active'
) AS src
ON target.goal_id = src.goal_id
WHEN MATCHED THEN UPDATE SET
    title = src.title,
    target_value = src.target_value,
    updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    goal_id, domain, title, metric_name, target_value, unit, comparison_operator, deadline, priority, status
) VALUES (
    src.goal_id, src.domain, src.title, src.metric_name, src.target_value, src.unit, src.comparison_operator, src.deadline, src.priority, src.status
);

MERGE INTO LUP_AI_DB.ANALYTICS.DIM_PREFERENCES AS target
USING (
    SELECT 'pref_sleep_floor' AS pref_id, 'habits_sleep' AS domain, 'min_sleep_hours' AS key_name,
           '{"min_hours": 6.5}' AS value_json, TRUE AS is_hard_constraint
    UNION ALL
    SELECT 'pref_privacy_mode', 'system', 'privacy_mode',
           '{"mode": "masked_cloud"}', TRUE
    UNION ALL
    SELECT 'pref_feedback_style', 'system', 'communication_style',
           '{"tone": "concise_evidence_first", "currency": "COP"}', FALSE
) AS src
ON target.pref_id = src.pref_id
WHEN MATCHED THEN UPDATE SET
    value_json = src.value_json,
    updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    pref_id, domain, key_name, value_json, is_hard_constraint
) VALUES (
    src.pref_id, src.domain, src.key_name, src.value_json, src.is_hard_constraint
);

