# Visión del Proyecto: LUP AI

**Estado:** `Implemented` (documento fundacional de visión — Fase 0)
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Propósito

**LUP AI** (*Personal Decision Intelligence Agent*) transforma datos personales cotidianos almacenados en **Snowflake** en decisiones accionables, cuantificadas y trazables a objetivos y preferencias del usuario.

## 2. Dominios Iniciales

1. **Finanzas:** Flujo de ingresos, gastos fijos, gasto discrecional y asignaciones de ahorro.
2. **Hábitos / Sueño:** Duración y calidad de sueño, actividad física, adherencia a hábitos diarios y nivel de energía percibido.
3. **Tiempo / Calendario:** Distribución de bloques de foco profundo (`DEEP_WORK`), reuniones, ocio y cumplimiento de agenda.

## 3. Principios Rectores

- **Cero IA local:** No se ejecuta ningún modelo LLM local; toda la capa local es determinista en Python y SQLite.
- **Privacidad previa determinista (`masked_cloud`):** Toda entidad identificable (PII) se enmascara deterministamente en Python antes de invocar a **Gemini 3.8**.
- **Cálculo determinista, lenguaje asistido:** Las estadísticas, anomalías y simulaciones What-If son ejecutadas exclusivamente por SQL en Snowflake y motores numéricos en Python (`pandas`, `numpy`, `scipy`).
- **Exposición API-First:** Backend desacoplado (`FastAPI`) + `CLI` desde el núcleo para habilitar interfaz visual en **Streamlit** en Fase 4 sin reescribir lógica de dominio.

