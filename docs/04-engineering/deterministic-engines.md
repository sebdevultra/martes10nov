# Motores Deterministas de Análisis, Simulación y Evidencia (Fase 2)

**Estado:** `Implemented`
**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Principio de Diseño

Ningún cálculo estadístico, detección de anomalías o simulación What-If se delega a Gemini 3.8. Toda la matemática reside en `src/lup_ai/engines/` usando `pandas`, `numpy` y `scipy`.

## 2. Componentes Implementados

### 2.1 `TrendAnalyzer` (`src/lup_ai/engines/trend_analyzer.py`)
- **Inputs:** Filas cronológicas de `SEMANTIC.VW_CROSS_DOMAIN_DAILY`.
- **Outputs:** `TrendAnalysisResult` con `MetricTrend` (medias 7d/30d/90d, variación WoW%, pendiente lineal, $R^2$, dirección y comparativa lunes-viernes vs. fin de semana) y `CorrelationItem` (Spearman con lag, marcado explícitamente `correlation_only=True`).

### 2.2 `AnomalyDetector` (`src/lup_ai/engines/anomaly_detector.py`)
- **Métodos estadísticos:** Mediana + MAD (Z-score modificado) e IQR sobre series continuas o valores positivos activos (para series dispersas como `discretionary_spend`).
- **Reglas de restricciones duras:** Rachas de $\ge 3$ noches consecutivas por debajo de `min_sleep_hours` configurado en `preferences_mirror`.
- **Oportunidades:** Excedente neto de flujo asignable a ahorro, días laborales con baja carga de reuniones aprovechables para `deep_work` y apalancamiento cruzado entre sueño ($\ge 7.5\text{h}$) y menor gasto discrecional siguiente.

### 2.3 `SimulationEngine` (`src/lup_ai/engines/simulation_engine.py`)
- **Método:** Bootstrap empírico / Monte Carlo reproducible (`numpy.random.default_rng(seed)`) sobre hasta 180 días de historia real del usuario.
- **Outputs:** `SimulationResult` con percentiles **Conservador (p25)**, **Base (p50)** y **Optimista (p75)**, probabilidad de éxito por meta activa, colisiones contra restricciones duras y presupuesto diario de 21h (`constraint_collisions`), y ranking de sensibilidad (`sensitivity_ranking`).

### 2.4 `RecommendationBuilder` (`src/lup_ai/engines/recommendation_builder.py`)
- **Contrato:** `RecommendationPayload` con lista obligatoria (`min_length=1`) de `RecommendationEvidence` (vista origen, rango de fechas, línea base, valor observado y Z-score/percentiles), alineación con metas, nivel de confianza (`high`/`medium`/`low` según tamaño de muestra) y trade-offs.

## 3. Frontera con Gemini 3.8

Cuando `GEMINI_API_KEY` está configurada, `LupOrchestrator` envía los reportes ya calculados y sanitizados a Gemini 3.8 para generar una síntesis en lenguaje natural (`answer`), pero mantiene bloqueados los objetos `simulation`, `trends`, `anomaly_scan` y `recommendations` provenientes de Python para garantizar **0 alucinación numérica**.

