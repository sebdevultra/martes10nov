-- Create RAW landing tables, CSV file format, and internal stage for the 3 core domains.
-- Domains: Finance, Habits/Sleep, and Time/Calendar.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;
USE SCHEMA RAW;

CREATE FILE FORMAT IF NOT EXISTS LUP_CSV_FORMAT
    TYPE = 'CSV'
    FIELD_DELIMITER = ','
    SKIP_HEADER = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    NULL_IF = ('', 'NULL', 'null')
    EMPTY_FIELD_AS_NULL = TRUE;

CREATE STAGE IF NOT EXISTS LUP_INTERNAL_STAGE
    FILE_FORMAT = LUP_CSV_FORMAT
    COMMENT = 'Internal stage for batch CSV/JSON ingestion into RAW tables';

CREATE TABLE IF NOT EXISTS RAW_FINANCE_TX (
    tx_id VARCHAR(64) NOT NULL,
    tx_timestamp TIMESTAMP_NTZ NOT NULL,
    amount NUMBER(14, 2) NOT NULL,
    currency VARCHAR(8) DEFAULT 'COP',
    direction VARCHAR(16) NOT NULL,
    category_raw VARCHAR(64) NOT NULL,
    merchant_raw VARCHAR(128),
    account_alias VARCHAR(64),
    is_discretionary BOOLEAN DEFAULT TRUE,
    notes_raw VARCHAR(512),
    source_system VARCHAR(32) DEFAULT 'synthetic',
    ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_raw_finance_tx PRIMARY KEY (tx_id)
);

CREATE TABLE IF NOT EXISTS RAW_HABITS_SLEEP (
    record_id VARCHAR(64) NOT NULL,
    log_date DATE NOT NULL,
    sleep_start_ts TIMESTAMP_NTZ,
    sleep_end_ts TIMESTAMP_NTZ,
    sleep_hours NUMBER(4, 2) NOT NULL,
    sleep_quality_score NUMBER(5, 2),
    exercise_minutes NUMBER(5, 0) DEFAULT 0,
    habit_completed_count NUMBER(3, 0) DEFAULT 0,
    habit_total_count NUMBER(3, 0) DEFAULT 0,
    energy_score NUMBER(4, 2),
    notes_raw VARCHAR(512),
    source_system VARCHAR(32) DEFAULT 'synthetic',
    ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_raw_habits_sleep PRIMARY KEY (record_id)
);

CREATE TABLE IF NOT EXISTS RAW_TIME_CALENDAR (
    event_id VARCHAR(64) NOT NULL,
    start_ts TIMESTAMP_NTZ NOT NULL,
    end_ts TIMESTAMP_NTZ NOT NULL,
    duration_minutes NUMBER(6, 0) NOT NULL,
    domain_tag VARCHAR(32) NOT NULL,
    block_type VARCHAR(32) NOT NULL,
    title_raw VARCHAR(256),
    priority VARCHAR(16) DEFAULT 'medium',
    completed_flag BOOLEAN DEFAULT TRUE,
    source_system VARCHAR(32) DEFAULT 'synthetic',
    ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_raw_time_calendar PRIMARY KEY (event_id)
);

