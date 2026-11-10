# Especificación de API REST y CLI: LUP AI

**Estado:** `Implemented` (Fase 1)
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Endpoints FastAPI (`src/lup_ai/api/main.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servicio, `privacy_mode` (`masked_cloud`), backend activo y configuración Snowflake/Gemini |
| `GET` | `/v1/goals` | Lista de objetivos activos y progreso más reciente |
| `GET` | `/v1/preferences` | Preferencias del usuario y restricciones duras (`is_hard_constraint`) |
| `GET` | `/v1/catalog/{domain}` | Consulta parametrizada por dominio (`finance`, `habits_sleep`, `time_calendar`, `cross_domain`, `goals`) |
| `POST` | `/v1/ask` | Orquestación completa con anonimización pre-LLM, catálogo/SQL Guard y síntesis con Gemini 3.8 o resumen determinista |

### Preparación para Streamlit (`ADR-003`)

`AskResponse` incluye `aggregates`, `evidence` y `chart_series` para que un cliente `Streamlit` (Fase 4) consuma y grafique series directamente vía HTTP sin acceder a módulos internos.

## 2. Comandos CLI (`src/lup_ai/cli/app.py`)

- `lup status`: Muestra configuración activa, modo de privacidad y estado de conectividad.
- `lup goals`: Tabla de objetivos activos y valores observados.
- `lup query <domain> --days 30`: Consulta agregados del catálogo por dominio.
- `lup ask "<pregunta>" [--session-id <id>]`: Ejecuta una consulta en lenguaje natural bajo `masked_cloud`.
- `lup serve [--host 127.0.0.1 --port 8000]`: Inicia el servidor FastAPI con Uvicorn.

