# Product Requirements Document (PRD): LUP AI

**Estado del documento:** `Implemented` (especificación base)
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Qué se construye, para quién y por qué

- **Qué:** Un agente de inteligencia de decisiones personales respaldado por Snowflake, motores estadísticos/simulación en Python, anonimización determinista y Gemini 3.8.
- **Para quién:** Usuario individual que centraliza su actividad diaria (Finanzas, Hábitos/Sueño y Tiempo/Calendario) y busca optimizar sus decisiones con evidencia cuantitativa.
- **Por qué:** Los tableros pasivos muestran datos históricos pero no conectan múltiples dominios ni evalúan escenarios hipotéticos contra restricciones reales de vida y objetivos activos.

## 2. Capacidades y Estado por Fase

| ID | Capacidad | Estado Actual | Fase Objetivo |
|---|---|---|---|
| `REQ-001` | Aprovisionamiento Snowflake desde cero (`LUP_AI_DB`) para 3 dominios + datos sintéticos | `Implemented` | Fase 0 |
| `REQ-002` | Configuración tipada, persistencia local SQLite y SQL Guard AST (`SEMANTIC.*`) | `Implemented` | Fase 0 |
| `REQ-003` | Anonimización determinista Python pre-LLM (`TextAnonymizer` con reglas deterministas y soporte Presidio) en modo `masked_cloud` | `Implemented` | Fase 1 |
| `REQ-004` | Consulta de datos personales (catálogo parametrizado + Text-to-SQL controlado con Gemini 3.8) vía CLI + FastAPI | `Implemented` | Fase 1 |
| `REQ-005` | Análisis determinista de comportamientos, tendencias y correlaciones cruzadas | `Planned` | Fase 2 |
| `REQ-006` | Detección estadística de anomalías y oportunidades (MAD/IQR/Z-score) | `Planned` | Fase 2 |
| `REQ-007` | Simulaciones hipotéticas What-If (Base / Conservador / Optimista) y recomendaciones justificadas | `Planned` | Fase 2 |
| `REQ-008` | Memoria de objetivos/preferencias, proactividad programada y Decision Outcome Loop | `Planned` | Fase 3 |
| `REQ-009` | Interfaz visual interactiva en Streamlit sobre la API REST | `Planned` | Fase 4 |

## 3. Requisitos No Funcionales Clave

- **Cero alucinación numérica:** Toda cifra en recomendaciones debe provenir de consultas Snowflake validadas o salidas del motor Python.
- **Aislamiento PII:** Ningún texto crudo identificable (`merchant_raw`, `notes_raw`, `title_raw`, `account_alias`) debe exponerse en `SEMANTIC.*` ni enviarse a Gemini 3.8 sin enmascaramiento determinista.
- **FinOps:** Control estricto de créditos Snowflake mediante Warehouse `XSMALL` (`AUTO_SUSPEND = 60`), `Resource Monitor` y caché TTL local.

