# Arquitectura de IA: LUP AI

**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Definición de Capas de Inteligencia

Por decisión arquitectónica explícita (`ADR-002`), **LUP AI no utiliza capa de IA local**.

| Componente | Tecnología | Rol | Estado |
|---|---|---|---|
| Anonimización pre-LLM | Python (`TextAnonymizer`: regex determinista + adaptador Presidio opcional) | Detección y pseudonimización reversible de PII antes de salir de local | `Implemented` |
| Cálculo analítico y simulación | Python (`pandas`, `numpy`, `scipy`) + Snowflake SQL | Tendencias, MAD/IQR/Z-score, simulaciones Monte Carlo What-If y recomendaciones con evidencia (`src/lup_ai/engines/`) | `Implemented` (motores base; integración `Planned`) |
| Única capa LLM | **Gemini 3.8** (`gemini-3.8-flash` configurable) | Interpretación semántica, Text-to-SQL controlado y síntesis justificada | `Implemented` |

## 2. Política Única `masked_cloud`

- `Settings.lup_privacy_mode` (`Implemented`) restringe la configuración únicamente al modo `masked_cloud`.
- Modos como `strict_local` o `full_hybrid` quedan excluidos por diseño.
- Todo payload hacia Gemini 3.8 atraviesa `TextAnonymizer` y la verificación `TextAnonymizer.assert_no_pii` antes del envío (`Implemented`).

## 3. Límites del LLM (No-Hallucination Contract)

- **Permitido para Gemini 3.8 (`Implemented` en `LupOrchestrator`):** Extracción estructurada de intención/parámetros (`IntentPlan`), generación de SQL sobre `SEMANTIC.*` validado por `sql_guard.py`, y redacción de recomendaciones (`SynthesizedAnswer`) basadas exclusivamente en evidencia numérica recibida.
- **Prohibido para Gemini 3.8:** Realizar aritmética libre, inventar métricas o consultar tablas fuera de `SEMANTIC.*`.

