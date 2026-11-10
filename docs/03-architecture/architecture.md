# Arquitectura del Sistema: LUP AI

**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Visión General

LUP AI adopta una arquitectura híbrida desacoplada sin capa de IA local:

- **Snowflake Data Cloud (`LUP_AI_DB`)** (`Implemented` DDL): Almacén analítico OLAP multi-dominio con capas `RAW`, `CORE`, `ANALYTICS` y `SEMANTIC`.
- **Núcleo Local Python + SQLite** (`Implemented` Fases 0–1; motores avanzados `Planned` Fase 2):
  - `Settings` (`src/lup_ai/config/settings.py`) — `Implemented`
  - `LocalStore` (`src/lup_ai/data/local_store.py`) — `Implemented`
  - `SQL Guard` (`src/lup_ai/data/sql_guard.py`) — `Implemented`
  - `SnowflakeAnalyticsRepository` y `LocalCsvAnalyticsRepository` — `Implemented`
  - `QueryCatalog` (`src/lup_ai/data/query_catalog.py`) — `Implemented`
  - `TextAnonymizer` (`src/lup_ai/privacy/text_anonymizer.py`) — `Implemented`
  - `ContextManager` (`src/lup_ai/memory/context_manager.py`) — `Implemented`
  - `LupOrchestrator` (`src/lup_ai/agent/orchestrator.py`) — `Implemented`
  - API REST (`FastAPI`) + CLI (`Typer`) — `Implemented`
  - Motores deterministas de tendencias, anomalías, simulación What-If y recomendaciones (`src/lup_ai/engines/`) — `Implemented` (módulos base; integración en orquestador/API/CLI `Planned` para continuación de Fase 2)
- **Única Capa LLM (`Gemini 3.8` en Cloud)** (`Implemented` cliente en `src/lup_ai/llm/gemini_client.py`): Recibe exclusivamente prompts sanitizados y resúmenes numéricos agregados bajo política `masked_cloud`, con fallback determinista de catálogo cuando no hay API key configurada.
- **Dashboard Visual (`Streamlit`)** (`Planned` Fase 4): Consumirá la API REST de FastAPI sin acoplar lógica de negocio a la UI.

## 2. Almacenamiento Dual (OLAP vs. OLTP)

| Responsabilidad | Tecnología | Estado |
|---|---|---|
| Históricos diarios, Dynamic Tables y vistas semánticas | Snowflake (`LUP_AI_DB`) | `Implemented` (DDLs + cliente `LUP_AGENT_RO`) |
| Espejo local de vistas semánticas para desarrollo offline | `LocalCsvAnalyticsRepository` | `Implemented` |
| Sesiones, caché TTL de KPIs, espejo de metas y mapa temporal de alias PII | SQLite (`LocalStore`) | `Implemented` |

## 3. Límites entre Servicios y Reglas de Dependencia

1. El runtime del agente en Snowflake solo puede operar con el rol `LUP_AGENT_RO` sobre el esquema `SEMANTIC`.
2. Todo SQL generado o parametrizado debe pasar por `validate_and_prepare_sql` (`sqlglot`) antes de ejecutarse.
3. Ninguna capa puede invocar a `Gemini 3.8` sin pasar previamente por `TextAnonymizer` y `TextAnonymizer.assert_no_pii` (`Implemented`).

