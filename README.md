# LUP AI — Personal Decision Intelligence Agent

Sistema inteligente que transforma datos cotidianos almacenados en **Snowflake** (en tres dominios iniciales: **Finanzas**, **Hábitos/Sueño** y **Tiempo/Calendario**) en decisiones accionables, cuantificadas y trazables a objetivos y preferencias personales.

## Estado Actual del Proyecto

- **Fase 0 (`Implemented`):** Inicialización del repositorio, documentación técnica en `docs/`, DDLs completos de Snowflake (`LUP_AI_DB`), generador determinista de datos sintéticos (180 días), configuración tipada, store local SQLite y validador SQL AST de solo lectura (`sql_guard`).
- **Fase 1 (`Implemented`):** Anonimizador determinista Python pre-LLM (`TextAnonymizer`), repositorio dual (`SnowflakeAnalyticsRepository` + `LocalCsvAnalyticsRepository`), catálogo parametrizado (`QueryCatalog`), gestor de contexto (`ContextManager`), integración `Gemini 3.8` (`masked_cloud`), orquestador (`LupOrchestrator`), API REST (`FastAPI`) y CLI (`Typer`).
- **Fase 2 (`Implemented`):** Motores deterministas de tendencias (`TrendAnalyzer`), anomalías/oportunidades (`AnomalyDetector`), simulación What-If (`SimulationEngine`) y recomendaciones justificadas (`RecommendationBuilder`), integrados en `LupOrchestrator`, FastAPI (`/v1/trends`, `/v1/anomalies`, `/v1/simulate`), CLI (`lup trends`, `lup anomalies`, `lup simulate`) y evals anti-alucinación (`tests/evals/`).
- **Fase 3 (`Implemented`):** Escaneo proactivo (`ProactiveScanner` / `lup-scan`), Decision Outcome Loop (`DecisionTracker`, registro de feedback y evaluación real de impacto) y captura incremental por lotes (`BatchIngestor`: buffer local en SQLite con flush batch a Snowflake `RAW` o CSV local).
- **Fase 4 (`Implemented` con fuentes simuladas):** Dashboard visual interactivo en **Streamlit** (`src/lup_ai/ui/streamlit_app.py` + `LupApiClient` consumiendo exclusivamente FastAPI) y conectores de fuentes externas simuladas (`SimulatedBankConnector`, `SimulatedWearableConnector`, `SimulatedCalendarConnector`).

## Arquitectura Resumida

- **Sin capa de IA local:** Toda anonimización de PII, validación SQL, caché y cálculo numérico corre deterministamente en Python y Snowflake.
- **Única capa LLM (`Gemini 3.8`):** Opera exclusivamente en modo `masked_cloud` sobre texto/contexto sanitizado y métricas agregadas.
- **Exposición API-First (`Implemented` Fases 1–4):** Backend `FastAPI` + `CLI (Typer)` + cliente visual `Streamlit` (`lup ui`).

## Primeros Pasos (Fases 0 a 4)

1. Crear entorno virtual e instalar dependencias:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[api,snowflake,llm,ui,dev]"
   ```
2. Copiar `.env.example` a `.env` y completar credenciales locales (no versionar `.env`).
3. Generar 180 días de datos sintéticos multi-dominio y poblar el espejo local SQLite:
   ```bash
   .venv/bin/lup-seed --output-dir data/generated --days 180 --seed 42 --sqlite-path data/lup_ai_local.db
   ```
4. Opcionalmente, en tu cuenta Snowflake, ejecutar `snowflake/ddl/01` a `05` y cargar `data/generated/` con `snowflake/seeds/load_synthetic_data.sql` (si no hay credenciales Snowflake configuradas, el sistema consulta automáticamente `data/generated/`).
5. Usar CLI y API REST:
   ```bash
   .venv/bin/lup status
   .venv/bin/lup goals
   .venv/bin/lup query finance --days 30
   .venv/bin/lup trends --days 90
   .venv/bin/lup anomalies --days 90
   .venv/bin/lup simulate --horizon-days 90 --discretionary-pct -0.25 --sleep-target 7.5
   .venv/bin/lup ask "Simula que pasa si reduzco gastos de ocio 25% y duermo 7.5h en 4 meses"
   .venv/bin/lup scan --days 90
   .venv/bin/lup decisions
   .venv/bin/lup capture-finance --amount 45000 --category DELIVERY
   .venv/bin/lup flush
   .venv/bin/lup sync-simulated --days 3
   .venv/bin/lup serve --host 127.0.0.1 --port 8000
   # En otra terminal:
   .venv/bin/lup ui --port 8501
   ```
6. Ejecutar pruebas y linter:
   ```bash
   .venv/bin/pytest -v
   .venv/bin/ruff check src tests
   ```

