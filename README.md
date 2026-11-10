# LUP AI — Personal Decision Intelligence Agent

Sistema inteligente que transforma datos cotidianos almacenados en **Snowflake** (en tres dominios iniciales: **Finanzas**, **Hábitos/Sueño** y **Tiempo/Calendario**) en decisiones accionables, cuantificadas y trazables a objetivos y preferencias personales.

## Estado Actual del Proyecto

- **Fase 0 (`Implemented`):** Inicialización del repositorio, documentación técnica en `docs/`, DDLs completos de Snowflake (`LUP_AI_DB`), generador determinista de datos sintéticos (180 días), configuración tipada, store local SQLite y validador SQL AST de solo lectura (`sql_guard`).
- **Fase 1 (`Implemented`):** Anonimizador determinista Python pre-LLM (`TextAnonymizer`), repositorio dual (`SnowflakeAnalyticsRepository` + `LocalCsvAnalyticsRepository`), catálogo parametrizado (`QueryCatalog`), gestor de contexto (`ContextManager`), integración `Gemini 3.8` (`masked_cloud`), orquestador (`LupOrchestrator`), API REST (`FastAPI`) y CLI (`Typer`).
- **Fase 2 (`In Progress`):** Motores deterministas de tendencias (`TrendAnalyzer`), anomalías/oportunidades (`AnomalyDetector`), simulación What-If (`SimulationEngine`) y recomendaciones justificadas (`RecommendationBuilder`) implementados y probados en `src/lup_ai/engines/`; pendiente su integración en `LupOrchestrator`, FastAPI, CLI y eval harness (ver `docs/04-engineering/next-session-plan.md`).
- **Fase 3–4 (`Planned`):** Proactividad, Decision Outcome Loop y dashboard visual en Streamlit.

## Arquitectura Resumida

- **Sin capa de IA local:** Toda anonimización de PII, validación SQL, caché y cálculo numérico corre deterministamente en Python y Snowflake.
- **Única capa LLM (`Gemini 3.8`):** Opera exclusivamente en modo `masked_cloud` sobre texto/contexto sanitizado y métricas agregadas.
- **Exposición API-First (`Implemented` Fase 1):** Backend `FastAPI` + `CLI (Typer)` preparados para conectar `Streamlit` en Fase 4.

## Primeros Pasos (Fases 0 y 1)

1. Crear entorno virtual e instalar dependencias:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[api,snowflake,llm,dev]"
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
   .venv/bin/lup ask "Como va mi sueno esta semana?"
   .venv/bin/lup serve --host 127.0.0.1 --port 8000
   ```
6. Ejecutar pruebas y linter:
   ```bash
   .venv/bin/pytest -v
   .venv/bin/ruff check src tests
   ```

