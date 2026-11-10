"""Read-only SQL AST guard restricting queries to curated SEMANTIC views."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_SEMANTIC_VIEWS: frozenset[str] = frozenset(
    {
        "VW_FINANCE_SUMMARY",
        "VW_HABITS_SLEEP_SUMMARY",
        "VW_TIME_CALENDAR_SUMMARY",
        "VW_CROSS_DOMAIN_DAILY",
        "VW_GOAL_PROGRESS",
    }
)

_FORBIDDEN_EXPRESSIONS = tuple(
    node
    for node in (
        getattr(exp, "Insert", None),
        getattr(exp, "Update", None),
        getattr(exp, "Delete", None),
        getattr(exp, "Drop", None),
        getattr(exp, "Create", None),
        getattr(exp, "Alter", None),
        getattr(exp, "Merge", None),
        getattr(exp, "Command", None),
        getattr(exp, "Grant", None),
        getattr(exp, "Revoke", None),
        getattr(exp, "Copy", None),
    )
    if node is not None
)


class SQLGuardError(ValueError):
    """Raised when an input SQL query violates read-only or semantic whitelist rules."""


def _validate_table_reference(
    table: exp.Table,
    cte_names: set[str],
    allowed_views: frozenset[str],
    allowed_database: str,
    allowed_schema: str,
) -> None:
    table_name = table.name.upper()
    if not table_name:
        raise SQLGuardError("Empty table reference is not permitted.")

    catalog = table.catalog.upper() if table.catalog else ""
    schema = table.db.upper() if table.db else ""

    # Allow references to CTE aliases only when unqualified.
    if not catalog and not schema and table_name in cte_names:
        return

    if catalog and catalog != allowed_database.upper():
        raise SQLGuardError(f"Database '{catalog}' is outside allowed '{allowed_database}'.")

    if schema and schema != allowed_schema.upper():
        raise SQLGuardError(f"Schema '{schema}' is not permitted; use '{allowed_schema}'.")

    if table_name not in allowed_views:
        raise SQLGuardError(
            f"View or table '{table_name}' is not in the allowed SEMANTIC whitelist."
        )


def _enforce_limit(tree: exp.Select, max_limit: int) -> exp.Select:
    limit_node = tree.args.get("limit")
    if limit_node is None:
        return tree.limit(max_limit)

    limit_expr = limit_node.expression
    if isinstance(limit_expr, exp.Literal) and limit_expr.is_int:
        requested = int(limit_expr.this)
        if requested <= 0:
            raise SQLGuardError("LIMIT must be a positive integer.")
        if requested > max_limit:
            return tree.limit(max_limit)
        return tree

    # Replace non-literal or dynamic LIMIT expressions with a deterministic cap.
    return tree.limit(max_limit)


def validate_and_prepare_sql(
    sql: str,
    allowed_views: frozenset[str] = ALLOWED_SEMANTIC_VIEWS,
    allowed_database: str = "LUP_AI_DB",
    allowed_schema: str = "SEMANTIC",
    max_limit: int = 200,
) -> str:
    """Validate that SQL is a single read-only SELECT over SEMANTIC views and cap LIMIT."""
    if not sql or not sql.strip():
        raise SQLGuardError("SQL query cannot be empty.")

    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except sqlglot.errors.ParseError as exc:
        raise SQLGuardError(f"Invalid SQL syntax: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise SQLGuardError("Exactly one SQL statement is permitted per query.")

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SQLGuardError("Only SELECT queries (optionally with WITH CTEs) are allowed.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_EXPRESSIONS):
            raise SQLGuardError(f"Forbidden SQL operation detected: {type(node).__name__}.")

    cte_names = {cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}

    tables = list(tree.find_all(exp.Table))
    if not tables:
        raise SQLGuardError("Query must reference at least one SEMANTIC view.")

    for table in tables:
        _validate_table_reference(
            table=table,
            cte_names=cte_names,
            allowed_views=allowed_views,
            allowed_database=allowed_database,
            allowed_schema=allowed_schema,
        )

    bounded_tree = _enforce_limit(tree, max_limit=max_limit)
    return bounded_tree.sql(dialect="snowflake")
