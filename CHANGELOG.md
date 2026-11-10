# Changelog

Todos los cambios relevantes del proyecto **LUP AI** se registran en este archivo usando zona horaria `America/Bogota` (`YYYY-MM-DD HH:mm`).

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

