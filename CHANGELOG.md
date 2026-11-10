# Changelog

Todos los cambios relevantes del proyecto **LUP AI** se registran en este archivo usando zona horaria `America/Bogota` (`YYYY-MM-DD HH:mm`).

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

