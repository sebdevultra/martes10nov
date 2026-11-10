# ADR-001: Almacenamiento Dual (Snowflake + SQLite) y Motores Analíticos Deterministas

- **ID:** ADR-001
- **Título:** Uso de almacenamiento dual (Snowflake OLAP + SQLite OLTP) y motores deterministas para cálculo
- **Fecha:** 2026-11-10
- **Estado:** Accepted

## Contexto
LUP AI procesa históricos personales multi-dominio (Finanzas, Hábitos/Sueño y Tiempo/Calendario) en Snowflake para consultas analíticas, detección de anomalías y simulaciones What-If.

## Problema
Usar Snowflake como base transaccional para sesiones de chat, caché y estado caliente genera latencia de *cold start* y gasto de créditos innecesario. Asimismo, delegar cálculos estadísticos o simulaciones al LLM introduce alucinaciones numéricas.

## Opciones Consideradas
1. **Snowflake para todo + cálculo en LLM:** Alta latencia, mayor costo y riesgo de errores aritméticos.
2. **Almacenamiento dual (Snowflake OLAP + SQLite local) + motores deterministas en Python/SQL:** Separa cargas analíticas de cargas OLTP y garantiza exactitud numérica.

## Decisión
Adoptar almacenamiento dual (Snowflake para históricos/snapshots/vistas semánticas y SQLite local para estado de sesión, caché TTL, espejo de metas y mapa temporal de alias PII) junto con motores deterministas en Python (`pandas`, `numpy`, `scipy`) y SQL para toda métrica, anomalía y simulación.

## Justificación
Minimiza el consumo de créditos en Snowflake (`XSMALL`, `AUTO_SUSPEND = 60`), ofrece respuestas instantáneas para estado local y asegura 0% de aritmética inventada por el LLM.

## Consecuencias
- Requiere mantener sincronizado el espejo local de objetivos/preferencias respecto a Snowflake.
- Exige contratos estrictos de datos entre el motor determinista y la capa de síntesis de Gemini 3.8.

