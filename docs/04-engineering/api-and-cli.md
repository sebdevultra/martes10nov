# Especificación de API REST y CLI: LUP AI

**Estado:** `Implemented` (Fases 1–4)
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Endpoints FastAPI (`src/lup_ai/api/main.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servicio, `privacy_mode` (`masked_cloud`), backend activo y configuración Snowflake/Gemini |
| `GET` | `/v1/goals` | Lista de objetivos activos y progreso más reciente |
| `GET` | `/v1/preferences` | Preferencias del usuario y restricciones duras (`is_hard_constraint`) |
| `GET` | `/v1/catalog/{domain}` | Consulta parametrizada por dominio (`finance`, `habits_sleep`, `time_calendar`, `cross_domain`, `goals`) |
| `GET` | `/v1/trends` | Análisis determinista de tendencias rolling (`7d`/`30d`/`90d`), pendiente y correlaciones cruzadas |
| `GET` | `/v1/anomalies` | Detección estadística de anomalías (MAD/IQR + rachas), oportunidades y recomendaciones con evidencia |
| `POST` | `/v1/simulate` | Simulación What-If Monte Carlo (p25/p50/p75), colisiones de restricciones y sensibilidad |
| `POST` | `/v1/ask` | Orquestación unificada (consultas, tendencias, anomalías o simulaciones) con anonimización pre-LLM |
| `GET` | `/v1/decisions` | Bandeja e historial de decisiones (`status`: `pending`, `accepted`, `dismissed`, `completed`) |
| `POST` | `/v1/decisions/{decision_id}/feedback` | Registra aceptación, descarte o nota de feedback del usuario |
| `GET` | `/v1/decisions/outcomes` | Métricas de adherencia (`AdherenceSummary`) e impactos evaluados |
| `POST` | `/v1/jobs/proactive-scan` | Ejecuta escaneo proactivo de anomalías/oportunidades y evaluación de outcomes maduros |
| `GET` | `/v1/ingest/pending` | Lista eventos en cola local pendientes de sincronización batch |
| `POST` | `/v1/ingest/capture` | Registra en cola local un evento de `finance`, `habits_sleep` o `time_calendar` |
| `POST` | `/v1/ingest/flush` | Sincroniza en lote la cola local hacia Snowflake `RAW` o espejo CSV local |
| `POST` | `/v1/connectors/simulate-sync` | Extrae feeds simulados externos (Banco, Wearable, Calendario) hacia `BatchIngestor` |

### Integración Streamlit (`ADR-003` — `Implemented`)

`src/lup_ai/ui/streamlit_app.py` utiliza `src/lup_ai/ui/api_client.py` (`LupApiClient`) para consumir los endpoints REST (`/v1/ask`, `/v1/trends`, `/v1/anomalies`, `/v1/simulate`, `/v1/decisions`, `/v1/ingest/capture`, `/v1/connectors/simulate-sync`) sin acceso directo a Snowflake ni a SQLite.

## 2. Comandos CLI (`src/lup_ai/cli/app.py`)

- `lup status`: Muestra configuración activa, modo de privacidad y estado de conectividad.
- `lup goals`: Tabla de objetivos activos y valores observados.
- `lup query <domain> --days 30`: Consulta agregados del catálogo por dominio.
- `lup trends [--days 90]`: Muestra tendencias y correlaciones cruzadas no causales.
- `lup anomalies [--days 90]`: Lista anomalías, oportunidades y recomendaciones justificadas.
- `lup simulate [--horizon-days 90 --discretionary-pct -0.25 --sleep-target 7.5 --deep-work-delta 4.0]`: Ejecuta simulación What-If.
- `lup ask "<pregunta>" [--session-id <id>]`: Ejecuta una consulta o simulación en lenguaje natural bajo `masked_cloud`.
- `lup decisions [--status pending]`: Muestra la bandeja de decisiones y tasas de aceptación/mejora.
- `lup feedback <decision_id> <accepted|dismissed> [--note "..."]`: Registra decisión del usuario.
- `lup scan [--days 90] [--force-outcomes]`: Ejecuta el escaneo proactivo (también disponible como `lup-scan`).
- `lup capture-finance`, `lup capture-sleep`, `lup capture-time`: Capturan eventos cotidianos en buffer local.
- `lup flush`: Sincroniza el buffer en lote hacia Snowflake `RAW` o el espejo CSV local.
- `lup sync-simulated [--days 3 --seed 101 --no-flush]`: Sincroniza fuentes externas simuladas.
- `lup ui [--port 8501]`: Lanza el dashboard interactivo en Streamlit.
- `lup serve [--host 127.0.0.1 --port 8000]`: Inicia el servidor FastAPI con Uvicorn.

