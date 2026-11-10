# ADR-003: Exposición API-First (FastAPI + CLI) Escalable a Streamlit

- **ID:** ADR-003
- **Título:** Diseño API-First con FastAPI y CLI desacoplados para escalar a Streamlit
- **Fecha:** 2026-11-10
- **Estado:** Accepted

## Contexto
El MVP de LUP AI se operará mediante CLI y API REST, con previsión de añadir una interfaz visual interactiva en Streamlit en Fase 4.

## Problema
Implementar lógica de negocio, conexiones Snowflake o llamadas LLM directamente dentro de scripts CLI o vistas Streamlit genera acoplamiento fuerte y obliga a reescribir flujos al incorporar la interfaz gráfica.

## Opciones Consideradas
1. **Monolito acoplado en Streamlit o CLI directo a módulos internos sin API.**
2. **Backend FastAPI con contratos `Pydantic` estables consumidos tanto por el CLI como por el futuro dashboard Streamlit.**

## Decisión
Exponer las capacidades del agente mediante contratos REST tipados en `FastAPI`, utilizando la misma capa de servicios desde el CLI y dejando preparada la integración directa de `Streamlit` como cliente HTTP en Fase 4.

## Justificación
Mantiene una única fuente de verdad para validación de entradas/salidas, seguridad y trazabilidad, y acelera la construcción de la interfaz visual en fases posteriores.

## Consecuencias
- Requiere definir desde Fase 1 esquemas `Pydantic` pensados para consumo visual (series, evidencias y rangos de simulación).

