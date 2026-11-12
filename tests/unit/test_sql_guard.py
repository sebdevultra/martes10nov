"""Unit tests for read-only AST SQL Guard."""

import pytest

from lup_ai.data.sql_guard import SQLGuardError, validate_and_prepare_sql


def test_allows_select_on_semantic_view_and_injects_limit() -> None:
    prepared = validate_and_prepare_sql(
        "SELECT snapshot_date, total_spend FROM SEMANTIC.VW_CROSS_DOMAIN_DAILY"
    )
    assert "VW_CROSS_DOMAIN_DAILY" in prepared
    assert "LIMIT 200" in prepared.upper()


def test_allows_with_cte_over_semantic_views_and_caps_limit() -> None:
    sql = """
    WITH weekly AS (
        SELECT week_start, SUM(total_amount) AS spend
        FROM LUP_AI_DB.SEMANTIC.VW_FINANCE_SUMMARY
        GROUP BY week_start
    )
    SELECT week_start, spend FROM weekly LIMIT 5000
    """
    prepared = validate_and_prepare_sql(sql, max_limit=200)
    assert "LIMIT 200" in prepared.upper()


@pytest.mark.parametrize(
    "bad_sql",
    [
        "",
        "DROP TABLE LUP_AI_DB.RAW.RAW_FINANCE_TX",
        "DELETE FROM SEMANTIC.VW_FINANCE_SUMMARY",
        "INSERT INTO SEMANTIC.VW_FINANCE_SUMMARY VALUES (1)",
        "SELECT * FROM RAW.RAW_FINANCE_TX",
        "SELECT * FROM CORE.CORE_TRANSACTIONS",
        "SELECT * FROM OTHER_DB.SEMANTIC.VW_FINANCE_SUMMARY",
        "SELECT * FROM SEMANTIC.VW_FINANCE_SUMMARY; DROP TABLE X;",
        "SELECT 1",
    ],
)
def test_rejects_unsafe_or_out_of_scope_queries(bad_sql: str) -> None:
    with pytest.raises(SQLGuardError):
        validate_and_prepare_sql(bad_sql)


def test_allows_pyformat_placeholders_only_when_explicitly_enabled() -> None:
    sql = (
        "SELECT tx_date, total_amount FROM SEMANTIC.VW_FINANCE_SUMMARY "
        "WHERE tx_date BETWEEN %s AND %s ORDER BY tx_date DESC"
    )
    with pytest.raises(SQLGuardError):
        validate_and_prepare_sql(sql)

    prepared = validate_and_prepare_sql(sql, allow_pyformat_params=True)
    assert "BETWEEN %s AND %s" in prepared
    assert "LIMIT 200" in prepared.upper()


def test_still_rejects_forbidden_table_with_pyformat_placeholders() -> None:
    with pytest.raises(SQLGuardError):
        validate_and_prepare_sql(
            "SELECT * FROM RAW.RAW_FINANCE_TX WHERE tx_date BETWEEN %s AND %s",
            allow_pyformat_params=True,
        )
