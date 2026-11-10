# Arquitectura de Datos: LUP AI

**Estado:** `Implemented` (DDLs Fase 0 y esquema `LocalStore`)
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Capas Medallion en Snowflake (`LUP_AI_DB`)

Los scripts en `snowflake/ddl/` implementan cuatro esquemas:

1. **`RAW`** (`02_raw_tables.sql`):
   - `RAW_FINANCE_TX`
   - `RAW_HABITS_SLEEP`
   - `RAW_TIME_CALENDAR`
   - Incluye `LUP_CSV_FORMAT` y `LUP_INTERNAL_STAGE`.
2. **`CORE`** (`03_core_models.sql`):
   - Vistas deduplicadas y normalizadas (`CORE_TRANSACTIONS`, `CORE_HABITS_SLEEP`, `CORE_TIME_BLOCKS`).
   - Excluye deliberadamente `merchant_raw`, `account_alias`, `notes_raw` y `title_raw` para aislar texto potencialmente identificable en `RAW`.
3. **`ANALYTICS`** (`04_analytics_dynamic_tables.sql`):
   - Tablas operativas: `DIM_GOALS`, `DIM_PREFERENCES`, `FCT_ANOMALIES`, `FCT_DECISIONS_LOG`, `FCT_DECISION_OUTCOMES`.
   - Dynamic Tables (`TARGET_LAG = '12 hours'`, `REFRESH_MODE = AUTO`): `FCT_DAILY_SNAPSHOT` y `FCT_WEEKLY_TRENDS`, unificando Finanzas, Hábitos/Sueño y Tiempo/Calendario por fecha (`snapshot_date` / `week_start`).
4. **`SEMANTIC`** (`05_semantic_views.sql`):
   - Única superficie permitida para `LUP_AGENT_RO` y `sql_guard.py`:
     - `VW_FINANCE_SUMMARY`
     - `VW_HABITS_SLEEP_SUMMARY`
     - `VW_TIME_CALENDAR_SUMMARY`
     - `VW_CROSS_DOMAIN_DAILY`
     - `VW_GOAL_PROGRESS`

## 2. Esquema Local SQLite (`LocalStore`)

Implementado en `src/lup_ai/data/local_store.py`:
- `sessions`, `chat_messages`: historial conversacional local.
- `metric_cache`: caché JSON con expiración TTL en UTC.
- `goals_mirror`, `preferences_mirror`: espejo local de objetivos y restricciones duras.
- `pii_alias_map`: diccionario temporal de alias por sesión para re-hidratación local sin exposición a Cloud.

## 3. Datos Sintéticos Iniciales

`src/lup_ai/seeds/generate_synthetic_data.py` (`Implemented`) genera un dataset determinista de 180 días (`finance_tx.csv`, `habits_sleep.csv`, `time_calendar.csv`, `goals.json`, `preferences.json`) con correlaciones entre dominios (p. ej. noches cortas de sueño incrementando gasto discrecional en domicilios y reduciendo foco profundo) y anomalías controladas para pruebas.

