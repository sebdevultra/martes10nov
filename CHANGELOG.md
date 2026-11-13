# Changelog

Todos los cambios relevantes del proyecto **LUP AI** se registran en este archivo usando zona horaria `America/Bogota` (`YYYY-MM-DD HH:mm`).

## [2026-11-13 08:15]

- **[Added]** Se implementó el Objetivo 1 con adaptadores base para fuentes reales (`src/lup_ai/connectors/real_sources.py` con `CSVBankConnector`, `WearableExportConnector`, `GoogleCalendarConnector` y `RealConnectorHub`), respetando los contratos tipados de `BatchIngestor` (`FinanceEventInput`, `HabitsSleepEventInput`, `TimeBlockEventInput`). Se expusieron endpoints FastAPI (`POST /v1/ingest/bank/csv`, `POST /v1/ingest/wearable/health`, `POST /v1/ingest/calendar/events`) y comando CLI `lup ingest-bank`, junto con pruebas en `tests/unit/test_real_connectors.py` e integración en `tests/integration/test_phase4_connectors_and_ui.py`.
  Motivo: Permitir la ingesta manual de extractos bancarios en CSV, exportaciones de wearables de salud y eventos de calendario directamente hacia el buffer local de batch ingestion.

- **[Added]** Se implementó el Objetivo 2 con CRUD completo de Metas y Preferencias bajo arquitectura FastAPI-First: modelos Pydantic v2 `GoalCreate`, `GoalUpdate`, `PreferenceCreate`, `PreferenceUpdate` en `src/lup_ai/api/schemas.py`, lógica de persistencia con sincronización dual SQLite/Snowflake (`ANALYTICS.DIM_GOALS` y `ANALYTICS.DIM_PREFERENCES`) en `LocalStore` (`src/lup_ai/data/local_store.py`), y endpoints REST en `src/lup_ai/api/main.py` (`POST /v1/goals`, `GET/PUT/DELETE /v1/goals/{id}` y equivalentes para `/v1/preferences`), con pruebas unitarias e integración en `tests/unit/test_local_store.py` y `tests/integration/test_phase2_endpoints.py`.
  Motivo: Permitir la gestión dinámica de metas y preferencias/restricciones con consistencia garantizada entre el espejo local y el almacén analítico.

- **[Added]** Se implementó el Objetivo 3 agregando `"apscheduler>=3.10.4"` bajo dependencias opcionales `api` en `pyproject.toml`, configurando la fecha ancla dinámica con `date.today()` en `ProactiveScanner` (`src/lup_ai/jobs/proactive_scan.py`) e integrando el context manager `lifespan` en `src/lup_ai/api/main.py` con `BackgroundScheduler` para ejecutar diariamente los escaneos proactivos (02:00) y flush de batches (02:30).
  Motivo: Automatizar los ciclos de escaneo proactivo y replicación batch mediante el ciclo de vida nativo de FastAPI.

- **[Added]** Se implementó el Objetivo 4 agregando métodos `get_unsynced_decisions`, `get_unsynced_outcomes`, `mark_decisions_as_synced` y `mark_outcomes_as_synced` en `LocalStore` (`src/lup_ai/data/local_store.py`), y conectando `BatchIngestor._flush_to_snowflake` para replicar idempotentemente (`MERGE INTO`) las recomendaciones y outcomes pendientes hacia `ANALYTICS.FCT_DECISIONS_LOG` y `ANALYTICS.FCT_DECISION_OUTCOMES` en Snowflake.
  Motivo: Garantizar la replicación analítica completa del Decision Outcome Loop hacia las tablas de hechos en Snowflake.

## [2026-11-12 11:18]

- **[Added]** Se integró `APScheduler` (`APScheduler>=3.10.4,<4.0.0` en `pyproject.toml`, módulo `src/lup_ai/jobs/scheduler.py` con `LupScheduler`, script `lup-scheduler`, comando CLI `lup scheduler`, endpoint `GET /v1/jobs/scheduler`, variables `LUP_SCHEDULER_*` en `Settings`/`.env.example` y ciclo de vida `lifespan` en `FastAPI`) para ejecutar periódicamente `ProactiveScanner.run` y `BatchIngestor.flush_batch`, junto con pruebas en `tests/unit/test_scheduler.py`.
  Motivo: Automatizar el escaneo proactivo y la sincronización por lotes sin depender de invocar `lup-scan` o `lup flush` manualmente.

- **[Added]** Se implementó la replicación idempotente (`MERGE INTO`) de `decisions_log` y `decision_outcomes` hacia `ANALYTICS.FCT_DECISIONS_LOG` y `ANALYTICS.FCT_DECISION_OUTCOMES` durante `BatchIngestor.flush_batch` (`src/lup_ai/data/batch_ingestor.py`), añadiendo control `synced_to_snowflake` en `LocalStore` (`src/lup_ai/data/local_store.py`) y pruebas en `tests/unit/test_decision_tracker_and_ingest.py`.
  Motivo: Persistir la trazabilidad completa de recomendaciones, acciones del usuario y evaluación de impacto en Snowflake al ejecutar cada flush batch.

- **[Fixed]** Se corrigió la fecha ancla por defecto a `date.today()` en `ProactiveScanner.run` (`src/lup_ai/jobs/proactive_scan.py`) y `DecisionTracker.evaluate_pending_outcomes` (`src/lup_ai/memory/decision_tracker.py`), actualizando `tests/integration/test_phase3_proactive_and_feedback.py`.
  Motivo: Evitar desfases temporales frente a `date(2026, 11, 10)` al ejecutar escaneos y evaluar outcomes en fechas reales posteriores.

## [2026-11-12 10:47]

- **[Added]** Se añadieron reintentos con backoff y respaldo configurable entre modelos Gemini (`gemini_fallback_models`) ante errores transitorios (`503 UNAVAILABLE`, `429`, `404`, timeouts) en `GeminiClient.generate_structured` (`src/lup_ai/llm/gemini_client.py`, `src/lup_ai/config/settings.py`), propagando `last_used_model` en `LupOrchestrator`, junto con pruebas unitarias en `tests/unit/test_gemini_client.py`.
  Motivo: Evitar caer al fallback determinista cuando el modelo principal configurado sufre picos intermitentes de demanda `503 UNAVAILABLE` en Google Gemini API.

## [2026-11-12 10:33]

- **[Fixed]** Se añadió degradación controlada ante errores transitorios de Gemini (como `503 UNAVAILABLE` o timeouts) en `LupOrchestrator._plan_intent` y `LupOrchestrator._synthesize_with_gemini_or_fallback` (`src/lup_ai/agent/orchestrator.py`), manteniendo `PrivacyViolationError` como fallo estricto (`fail-closed`), con prueba de integración en `tests/integration/test_orchestrator_and_api.py`.
  Motivo: Garantizar que `lup ask` y `POST /v1/ask` entreguen el análisis determinista respaldado por Snowflake incluso cuando el endpoint Cloud de Gemini presente picos temporales de demanda.

## [2026-11-12 10:23]

- **[Fixed]** Se actualizó `validate_and_prepare_sql` en `src/lup_ai/data/sql_guard.py` con soporte opcional `allow_pyformat_params=True` para placeholders `%s` (manteniéndolos bloqueados por defecto en SQL abierto) y se normalizaron valores `Decimal` y `date`/`datetime` en `SnowflakeAnalyticsRepository._run_query` (`src/lup_ai/data/snowflake_client.py`), añadiendo pruebas en `tests/unit/test_sql_guard.py`.
  Motivo: Evitar que `sqlglot` interprete `%s` como operador módulo (`Mod`) al validar consultas parametrizadas del catálogo contra Snowflake y garantizar compatibilidad numérica/JSON en caché local.

## [2026-11-12 10:08]

- **[Fixed]** Se corrigió `snowflake/ddl/01_databases_warehouses_roles.sql` para seleccionar `USE WAREHOUSE LUP_WH_XS` bajo `SYSADMIN` y envolver `CURRENT_USER()` entre comillas dobles (`'"' || REPLACE(CURRENT_USER(), '"', '""') || '"'`) en `LUP_TARGET_USER` antes de ejecutar `GRANT ROLE ... TO USER IDENTIFIER($LUP_TARGET_USER)`.
  Motivo: Evitar el error SQL `002003 (User '...' does not exist or not authorized)` cuando el usuario de Snowflake tiene identificador en minúsculas o mixto, ya que `IDENTIFIER(...)` sin comillas dobles convierte la cadena a mayúsculas.

## [2026-11-12 10:01]

- **[Fixed]** Se actualizó `build_snowflake_connect_kwargs` en `src/lup_ai/data/snowflake_client.py` para autenticar llaves `.p8` con `authenticator="SNOWFLAKE_JWT"`, `private_key_file` (con expansión de `~` y validación de existencia) y `private_key_password`, añadiendo pruebas unitarias en `tests/unit/test_bootstrap_snowflake.py`.
  Motivo: Evitar el error `390144 (JWT token is invalid)` en `snowflake-connector-python` 5.2.0 al enviar DER bytes sin `authenticator="SNOWFLAKE_JWT"`, unificando la conexión por llave `.p8` en `bootstrap-snowflake`, `SnowflakeAnalyticsRepository` y `BatchIngestor`.

## [2026-11-10 12:28]

- **[Added]** Se implementó el automatizador de aprovisionamiento Snowflake (`src/lup_ai/seeds/bootstrap_snowflake.py`, comando CLI `lup bootstrap-snowflake`) con pruebas unitarias (`tests/unit/test_bootstrap_snowflake.py`) y asignación automática de roles `LUP_INGEST_RW` y `LUP_AGENT_RO` al usuario actual en `snowflake/ddl/01_databases_warehouses_roles.sql`.
  Motivo: Permitir ejecutar DDLs `01` a `05`, subir CSVs al stage con `PUT`, cargar `load_synthetic_data.sql` y refrescar Dynamic Tables directamente desde la terminal sin requerir SnowSQL ni pasos manuales en Snowsight.

- **[Fixed]** Se unificó la detección de credenciales no-placeholder (`has_valid_snowflake_credentials`) y construcción de conexión (`build_snowflake_connect_kwargs`) en `src/lup_ai/data/snowflake_client.py` reutilizada por `BatchIngestor`.
  Motivo: Evitar que valores plantilla de `.env.example` se interpreten como credenciales reales y soportar autenticación con contraseña o llave privada de manera uniforme.

## [2026-11-10 12:19]

- **[Added]** Se implementó la Fase 4 con fuentes externas simuladas y dashboard visual en Streamlit: conectores simulados (`src/lup_ai/connectors/simulated_sources.py`: `SimulatedBankConnector`, `SimulatedWearableConnector`, `SimulatedCalendarConnector`, `SimulatedConnectorHub`), cliente HTTP desacoplado (`src/lup_ai/ui/api_client.py`), dashboard interactivo (`src/lup_ai/ui/streamlit_app.py`), endpoint `POST /v1/connectors/simulate-sync`, comandos CLI `lup sync-simulated` y `lup ui`, y pruebas unitarias e integración con `streamlit.testing.v1.AppTest` (`tests/unit/test_simulated_connectors.py` y `tests/integration/test_phase4_connectors_and_ui.py`).
  Motivo: Completar la capa de visualización interactiva desacoplada vía API REST y habilitar el flujo completo de conectores externos usando feeds simulados de Banco, Wearable y Calendario antes de integrar credenciales reales.

- **[Docs]** Se actualizaron `README.md`, `docs/01-product/prd.md`, `docs/03-architecture/architecture.md`, `docs/04-engineering/api-and-cli.md` y `docs/04-engineering/next-session-plan.md`.
  Motivo: Mantener sincronizada toda la documentación con la finalización de la Fase 4 y el estado real verificado del proyecto.

## [2026-11-10 12:12]

- **[Added]** Se implementó la Fase 3 completa: Decision Outcome Loop (`src/lup_ai/memory/decision_tracker.py`), escaneo proactivo (`src/lup_ai/jobs/proactive_scan.py` + script `lup-scan`), captura incremental en cola SQLite con sincronización batch (`src/lup_ai/data/batch_ingestor.py`), tablas locales `decisions_log`, `decision_outcomes` e `ingest_buffer` en `LocalStore`, inyección de adherencia en `ContextManager`, nuevos endpoints FastAPI (`/v1/decisions`, `/v1/decisions/{id}/feedback`, `/v1/decisions/outcomes`, `/v1/jobs/proactive-scan`, `/v1/ingest/capture`, `/v1/ingest/pending`, `/v1/ingest/flush`), comandos CLI (`decisions`, `feedback`, `scan`, `capture-finance`, `capture-sleep`, `capture-time`, `flush`) y pruebas en `tests/unit/test_decision_tracker_and_ingest.py` y `tests/integration/test_phase3_proactive_and_feedback.py`.
  Motivo: Convertir el sistema en un ciclo proactivo cerrado que detecta anomalías sin consulta manual, captura feedback real, evalúa impacto tras la ventana de observación y acumula registros cotidianos localmente para minimizar despertares del warehouse en Snowflake.

- **[Docs]** Se actualizaron `README.md`, `docs/01-product/prd.md`, `docs/03-architecture/architecture.md`, `docs/03-architecture/data-architecture.md`, `docs/04-engineering/api-and-cli.md` y `docs/04-engineering/next-session-plan.md`.
  Motivo: Mantener sincronizada la documentación con el estado `Implemented` de Fase 3 y dejar únicamente la Fase 4 (UI Streamlit y conectores reales) como `Planned`.

## [2026-11-10 12:03]

- **[Added]** Se completó la Fase 2 integrando `TrendAnalyzer`, `AnomalyDetector`, `SimulationEngine` y `RecommendationBuilder` en `src/lup_ai/agent/orchestrator.py` (`analyze_trends`, `scan_anomalies`, `simulate` y enrutamiento en `ask`), añadiendo los endpoints `GET /v1/trends`, `GET /v1/anomalies`, `POST /v1/simulate` en FastAPI (`src/lup_ai/api/`), los comandos `lup trends`, `lup anomalies` y `lup simulate` en `src/lup_ai/cli/app.py`, las pruebas de integración (`tests/integration/test_phase2_endpoints.py`) y la suite de evaluación anti-alucinación (`tests/evals/test_zero_hallucination_evals.py`).
  Motivo: Habilitar análisis determinista de tendencias, detección estadística de anomalías/oportunidades, simulaciones What-If con percentiles (p25/p50/p75) y recomendaciones trazables garantizando 0 cálculo libre en Gemini 3.8 y 0 fuga de PII.

- **[Docs]** Se añadió `docs/04-engineering/deterministic-engines.md` y se actualizaron `README.md`, `docs/01-product/prd.md`, `docs/03-architecture/architecture.md`, `docs/04-engineering/api-and-cli.md`, `docs/04-engineering/next-session-plan.md` y `docs/05-ai/ai-architecture.md`.
  Motivo: Reflejar la finalización de la Fase 2 y mantener sincronizado el estado del proyecto (`Implemented` vs `Planned` para Fases 3 y 4).

## [2026-11-10 10:07]

- **[Added]** Se implementaron los motores deterministas base de Fase 2 en `src/lup_ai/engines/` (`trend_analyzer.py`, `anomaly_detector.py`, `simulation_engine.py`, `recommendation_builder.py`) y sus pruebas en `tests/unit/test_engines.py`.
  Motivo: Ejecutar tendencias, detección de anomalías/oportunidades (MAD/IQR y restricciones duras), simulaciones What-If Monte Carlo (p25/p50/p75) y construcción de recomendaciones con evidencia numérica fuera del LLM.

- **[Docs]** Se creó el plan de continuación para nueva sesión y publicación en GitHub (`docs/04-engineering/next-session-plan.md`) y se sincronizaron `README.md`, `docs/03-architecture/architecture.md` y `docs/05-ai/ai-architecture.md`.
  Motivo: Dejar el repositorio limpio, verificado y documentado con el estado exacto antes de subirlo a GitHub y retomar la integración de Fase 2 desde cero en una nueva sesión de Antigravity.

## [2026-11-10 09:51]

- **[Added]** Se implementó la Fase 1 completa: anonimizador determinista pre-LLM (`src/lup_ai/privacy/text_anonymizer.py`), repositorios `SnowflakeAnalyticsRepository` y `LocalCsvAnalyticsRepository` (`src/lup_ai/data/`), catálogo parametrizado (`query_catalog.py`), gestor de contexto (`context_manager.py`), cliente `Gemini 3.8` en modo único `masked_cloud` (`gemini_client.py`), orquestador (`orchestrator.py`), API REST en `FastAPI` (`src/lup_ai/api/`) y CLI en `Typer` (`src/lup_ai/cli/`).
  Motivo: Habilitar consultas seguras sobre Finanzas, Hábitos/Sueño y Tiempo/Calendario tanto contra Snowflake como contra el espejo sintético local, garantizando enmascaramiento determinista de PII antes de cualquier envío cloud y contratos HTTP listos para escalar a Streamlit.

- **[Changed]** Se actualizó `README.md`, `pyproject.toml` (script `lup`) y la documentación en `docs/` (`prd.md`, `architecture.md`, `ai-architecture.md`, `security-and-privacy.md`, y nuevo `docs/04-engineering/api-and-cli.md`) junto con pruebas unitarias y de integración en `tests/`.
  Motivo: Mantener sincronizada la documentación con las capacidades implementadas en Fase 1 y verificar mediante pruebas automatizadas la barrera anti-PII y la recuperación ante SQL inseguro.

## [2026-11-10 09:35]

- **[Added]** Se inicializó la estructura base del proyecto en `/home/Coder/Escritorio/sebas/martes10nov` (`pyproject.toml`, `.env.example`, `.gitignore`, `README.md`) y la documentación técnica inicial en `docs/` (`00-vision/`, `01-product/`, `03-architecture/`, `05-ai/`, `06-security/` y `09-decisions/ADR-001` a `ADR-003`).
  Motivo: Establecer los fundamentos del proyecto, trazabilidad de decisiones arquitectónicas (cero IA local, anonimización determinista en Python pre-LLM, modo único `masked_cloud`, 3 dominios iniciales y exposición API-First) y configuración reproducible para la Fase 0.

- **[Added]** Se crearon los scripts DDL de Snowflake (`snowflake/ddl/01_databases_warehouses_roles.sql` a `05_semantic_views.sql`) y el script de carga (`snowflake/seeds/load_synthetic_data.sql`) para `LUP_AI_DB` con esquemas `RAW`, `CORE`, `ANALYTICS` y `SEMANTIC`, roles separados (`LUP_INGEST_RW` y `LUP_AGENT_RO`), `Resource Monitor` y Dynamic Tables para Finanzas, Hábitos/Sueño y Tiempo/Calendario.
  Motivo: Aprovisionar desde cero la cuenta Snowflake con mínimo privilegio, control de créditos (`XSMALL`, `AUTO_SUSPEND = 60`) y aislamiento de PII fuera de la capa `SEMANTIC`.

- **[Added]** Se implementaron los módulos base de Fase 0 (`src/lup_ai/config/settings.py`, `src/lup_ai/data/local_store.py`, `src/lup_ai/data/sql_guard.py`, `src/lup_ai/seeds/generate_synthetic_data.py`) junto con sus pruebas unitarias en `tests/unit/`.
  Motivo: Proveer configuración tipada con política única `masked_cloud`, almacenamiento local SQLite (caché TTL, espejo de objetivos/preferencias y mapa de alias PII), validación AST read-only con `sqlglot` y un generador determinista de 180 días sintéticos multi-dominio.

