# Plan de Continuación en Nueva Sesión (GitHub + Antigravity desde 0)

**Fecha de corte:** 2026-11-10 10:07 (America/Bogota)
**Estado del repositorio:** Listo para inicializar Git, subir a GitHub y retomar en una nueva sesión de Antigravity.

---

## 1. Resumen Ejecutivo para la Nueva Sesión

Si acabas de abrir este repositorio en una nueva sesión de Antigravity, este documento contiene el estado exacto del proyecto **LUP AI**, las decisiones arquitectónicas ya cerradas, lo que falta implementar y el orden recomendado para continuar sin repetir trabajo ni contradecir el diseño acordado.

### Decisiones Arquitectónicas Cerradas (No reabrir sin ADR nuevo)

1. **Cero IA local:** No se usa Ollama ni ningún modelo LLM local (`ADR-002`).
2. **Privacidad determinista pre-LLM en modo único `masked_cloud`:** Toda sanitización de PII ocurre en `src/lup_ai/privacy/text_anonymizer.py` antes de invocar a **Gemini 3.8** (`ADR-002`). Modos `strict_local` y `full_hybrid` están eliminados.
3. **3 dominios iniciales:** Finanzas (`finance`), Hábitos/Sueño (`habits_sleep`) y Tiempo/Calendario (`time_calendar`).
4. **Almacenamiento Dual + Motores Deterministas (`ADR-001`):**
   - **Snowflake (`LUP_AI_DB`)** como DW OLAP (`RAW` -> `CORE` -> `ANALYTICS` -> `SEMANTIC`) con rol `LUP_AGENT_RO` de solo lectura sobre `SEMANTIC.*`.
   - **SQLite local (`LocalStore`)** para sesiones, caché TTL, espejo de metas/preferencias y mapa reversible de alias PII (`pii_alias_map`).
   - **Motores Python deterministas** (`pandas`, `numpy`, `scipy`) para tendencias, anomalías y simulaciones What-If (el LLM jamás calcula).
5. **Exposición API-First (`ADR-003`):** Backend `FastAPI` + `CLI (Typer)` hoy, escalable a dashboard visual en `Streamlit` en Fase 4.

---

## 2. Estado Exacto de Implementación

### Ya Implementado y Verificado (`Implemented`)

- **Fase 0 — Fundamentos y Snowflake desde cero:**
  - `snowflake/ddl/01_databases_warehouses_roles.sql` a `05_semantic_views.sql` (`LUP_AI_DB`, `LUP_WH_XS`, `LUP_MONTHLY_RM`, `LUP_INGEST_RW` / `LUP_AGENT_RO`, Dynamic Tables y 5 vistas `SEMANTIC.*`).
  - `snowflake/seeds/load_synthetic_data.sql` y `src/lup_ai/seeds/generate_synthetic_data.py` (180 días sintéticos multi-dominio).
  - `src/lup_ai/config/settings.py`, `src/lup_ai/data/local_store.py`, `src/lup_ai/data/sql_guard.py` (`sqlglot` read-only AST guard).
- **Fase 1 — Privacidad, Datos Duales, Gemini 3.8, CLI y API REST:**
  - `src/lup_ai/privacy/text_anonymizer.py` (`TextAnonymizer` con `assert_no_pii` y re-hidratación local).
  - `src/lup_ai/data/snowflake_client.py` y `src/lup_ai/data/local_csv_repo.py` (espejo local offline de las 5 vistas semánticas).
  - `src/lup_ai/data/query_catalog.py` y `src/lup_ai/memory/context_manager.py`.
  - `src/lup_ai/llm/gemini_client.py` (`GeminiClient` en modo `masked_cloud`).
  - `src/lup_ai/agent/orchestrator.py` (`LupOrchestrator`), `src/lup_ai/api/main.py` (`FastAPI`) y `src/lup_ai/cli/app.py` (`lup`).
- **Fase 2 (Parte A) — Núcleo de Motores Deterministas:**
  - `src/lup_ai/engines/trend_analyzer.py` (`TrendAnalyzer`: medias 7d/30d/90d, WoW, pendiente, $R^2$, correlaciones Spearman con lag).
  - `src/lup_ai/engines/anomaly_detector.py` (`AnomalyDetector`: MAD/IQR, rachas contra `hard_constraint` de sueño y oportunidades).
  - `src/lup_ai/engines/simulation_engine.py` (`SimulationEngine`: bootstrap Monte Carlo p25/p50/p75, probabilidad de metas, colisiones y sensibilidad).
  - `src/lup_ai/engines/recommendation_builder.py` (`RecommendationBuilder`: contrato obligatorio con `evidence` numérica).
  - Pruebas unitarias en `tests/unit/test_engines.py`.

### Pendiente por Implementar (`Planned`)

1. **Fase 2 (Parte B — Integración y Evals):**
   - Conectar `TrendAnalyzer`, `AnomalyDetector`, `SimulationEngine` y `RecommendationBuilder` dentro de `LupOrchestrator.ask` (detección de intenciones de tendencias, anomalías/oportunidades y escenarios What-If).
   - Exponer en `FastAPI` (`src/lup_ai/api/main.py` y `schemas.py`): `GET /v1/trends`, `GET /v1/anomalies` y `POST /v1/simulate`.
   - Exponer en `CLI` (`src/lup_ai/cli/app.py`): `lup trends`, `lup anomalies`, `lup simulate`.
   - Crear `tests/evals/test_zero_hallucination_evals.py` verificando 0 números inventados y 0 fuga de PII en simulaciones.
2. **Fase 3 — Proactividad y Decision Outcome Loop:**
   - `src/lup_ai/jobs/proactive_scan.py` (escaneo programado y bandeja local de decisiones).
   - Registro y evaluación de impacto en `FCT_DECISIONS_LOG` y `FCT_DECISION_OUTCOMES` + endpoints de feedback (`POST /v1/decisions/{id}/feedback`).
   - Buffer local en SQLite para captura rápida de registros cotidianos y sincronización batch a Snowflake.
3. **Fase 4 — Interfaz Visual en Streamlit y Fuentes Reales:**
   - Aplicación `Streamlit` consumiendo únicamente la API REST de `FastAPI` (`/v1/ask`, `/v1/trends`, `/v1/anomalies`, `/v1/simulate`, `/v1/goals`).
   - Conectores/importadores para datos reales del usuario (extractos financieros, salud/sueño, calendario).

---

## 3. Pasos Previos: Subir el Repositorio a GitHub

`.gitignore` ya excluye `.venv/`, `.env`, `*.db`, `data/generated/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` y llaves privadas (`*.pem`, `*.p8`, `*.key`).

Ejecuta desde `/home/Coder/Escritorio/sebas/martes10nov`:

```bash
# 1. Verificar tests y linter antes del primer commit
.venv/bin/pytest -v
.venv/bin/ruff check src tests

# 2. Inicializar Git y revisar exactamente que archivos se van a subir
git init
git branch -M main
git status

# 3. Verificar que NO aparezcan .env, .venv, data/generated ni *.db en git status
git add .
git commit -m "feat: initialize LUP AI Phase 0, Phase 1, and Phase 2 deterministic engines"

# 4. Conectar con tu repositorio remoto en GitHub y hacer push
git remote add origin https://github.com/<TU_USUARIO>/<TU_REPO>.git
git push -u origin main
```

### Checklist de Entorno al Clonar en Otra Máquina o Sesión

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[api,snowflake,llm,dev]"
cp .env.example .env
# Completar credenciales de Snowflake y GEMINI_API_KEY en .env (opcional para desarrollo offline)
.venv/bin/lup-seed --output-dir data/generated --days 180 --seed 42 --sqlite-path data/lup_ai_local.db
.venv/bin/pytest -v
```

*(Opcional para conectar Snowflake en vivo)*: ejecutar en orden `snowflake/ddl/01` a `05` en tu cuenta Snowflake, cargar `data/generated/*.csv` al stage `@RAW.LUP_INTERNAL_STAGE` y correr `snowflake/seeds/load_synthetic_data.sql`.

---

## 4. Prompt Listo para Copiar en la Nueva Sesión de Antigravity

Cuando abras la nueva sesión de Antigravity en este repositorio, inicia con:

> Lee `docs/04-engineering/next-session-plan.md`, `README.md`, `CHANGELOG.md` y los ADRs en `docs/09-decisions/`. Verifica el estado actual con `.venv/bin/pytest -v` y `.venv/bin/ruff check src tests`, y continúa completando la **Fase 2 (Parte B)**: integra `src/lup_ai/engines/` en `LupOrchestrator`, añade los endpoints `GET /v1/trends`, `GET /v1/anomalies` y `POST /v1/simulate` en FastAPI, los comandos equivalentes en el CLI `lup`, la suite de evaluación anti-alucinación en `tests/evals/` y actualiza `docs/` y `CHANGELOG.md`.

