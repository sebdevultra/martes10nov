# ADR-002: Sin Capa de IA Local; Anonimización Determinista en Python y Modo Único `masked_cloud`

- **ID:** ADR-002
- **Título:** Eliminación de capa LLM local; anonimización determinista en Python previa a Gemini 3.8 en modo `masked_cloud`
- **Fecha:** 2026-11-10
- **Estado:** Accepted

## Contexto
Se evaluó inicialmente combinar un modelo local de 8B (Ollama) con un modelo cloud (Gemini 3.8) y ofrecer múltiples modos de privacidad (`strict_local`, `masked_cloud`, `full_hybrid`).

## Problema
Mantener inferencia LLM local añade consumo significativo de RAM/VRAM, fragilidad en *tool calling* y complejidad operativa en el router, mientras que delegar anonimización de PII a un modelo pequeño no garantiza comportamiento determinista ni auditable.

## Opciones Consideradas
1. **Híbrido Ollama 8B + Gemini 3.8 con tres modos de privacidad.**
2. **Cero IA local; anonimización determinista en Python + Gemini 3.8 como única capa LLM bajo modo único `masked_cloud`.**

## Decisión
Prescindir por completo de cualquier capa de IA local. Toda detección, enmascaramiento y pseudonimización reversible de PII se realiza mediante librerías deterministas de procesamiento de texto en Python (`Presidio` + `spaCy` + `Regex`) antes de conectar con **Gemini 3.8**, operando exclusivamente bajo la política `masked_cloud`.

## Justificación
Reduce la complejidad de infraestructura local, hace auditable y determinista el control de privacidad, y concentra el razonamiento semántico en un único modelo cloud capaz de manejar esquemas complejos y análisis multi-dominio sobre contexto ya sanitizado.

## Consecuencias
- La generación de respuestas conversacionales complejas y Text-to-SQL ad-hoc requiere conectividad hacia Gemini 3.8.
- Toda consulta determinista del catálogo y cálculos locales pueden seguir operando localmente sin LLM.

