# Seguridad, Privacidad y FinOps: LUP AI

**Última actualización:** 2026-11-10 (America/Bogota)

## 1. Control de Acceso y Roles en Snowflake (`Implemented` en DDL)

Definido en `snowflake/ddl/01_databases_warehouses_roles.sql`:

- `LUP_INGEST_RW`: Rol exclusivo para scripts de ingesta y mantenimiento DDL en `RAW`, `CORE`, `ANALYTICS` y `SEMANTIC`.
- `LUP_AGENT_RO`: Rol de mínimo privilegio utilizado en runtime por el agente; solo posee `USAGE` sobre `LUP_WH_XS`/`LUP_AI_DB`/`SEMANTIC` y `SELECT` sobre las vistas de `SEMANTIC`. Carece de acceso a `RAW` y de permisos DML/DDL.

## 2. Validación SQL AST (`Implemented`)

`src/lup_ai/data/sql_guard.py` impone sobre toda consulta antes de enviarla a Snowflake:
- Un único statement por ejecución.
- Solo `SELECT` (con `WITH` CTE opcionales); rechazo explícito de `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `MERGE`, `COPY`, `GRANT`, `REVOKE`.
- Whitelist estricta de las 5 vistas en `SEMANTIC.*` (`ALLOWED_SEMANTIC_VIEWS`).
- Límite máximo de filas (`LIMIT 200` por defecto) y timeout de sesión configurado (`15s`).

## 3. Aislamiento y Anonimización de PII

- **En Snowflake (`Implemented`):** Columnas con texto libre potencialmente identificable (`merchant_raw`, `account_alias`, `notes_raw`, `title_raw`) quedan aisladas en `RAW` y se omiten en `CORE`, `ANALYTICS` y `SEMANTIC`.
- **En Python pre-LLM (`Implemented`):** `src/lup_ai/privacy/text_anonymizer.py` ejecuta reglas deterministas (más adaptador opcional Presidio si está instalado), almacena el mapa reversible en `pii_alias_map` (SQLite local) y ejecuta `assert_no_pii` en `GeminiClient.generate_structured` antes de cada petición cloud en modo único `masked_cloud`.

## 4. FinOps y Gestión de Secretos

- Warehouse `LUP_WH_XS` tamaño `XSMALL`, `AUTO_SUSPEND = 60` y `Resource Monitor LUP_MONTHLY_RM` con notificación al 75% y suspensión automática al 100% (`Implemented` en DDL).
- Caché TTL local en `LocalStore` (`Implemented`) para evitar activaciones repetidas del warehouse.
- `.env` y llaves privadas (`*.pem`, `*.p8`, `*.key`) excluidos en `.gitignore` (`Implemented`). Nunca registrar API keys ni credenciales en logs o documentación.

