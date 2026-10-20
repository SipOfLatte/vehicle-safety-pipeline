"""Runs checked SQL as the read-only ASK_ROLE with a statement timeout and a row cap."""

from __future__ import annotations

import datetime as dt
import decimal
from dataclasses import dataclass

from ai.snowflake_conn import connect

ROLE, WAREHOUSE = "ASK_ROLE", "APP_WH"
STATEMENT_TIMEOUT_SECONDS = 30
MAX_ROWS = 1000


def ask_connection(role: str = ROLE):
    """A connection for model-written SQL: read-only role, GOLD as the default schema, 30s timeout."""
    conn = connect(role=role, warehouse=WAREHOUSE)
    cur = conn.cursor()
    # Without this the session also carries the user's other roles (Snowflake's default secondary roles
    # are ALL), so model-written SQL could reach APP_ROLE's usage log and Cortex.
    cur.execute("use secondary roles none")
    cur.execute(f"alter session set statement_timeout_in_seconds = {STATEMENT_TIMEOUT_SECONDS}, query_tag = 'text_to_sql'")
    cur.execute("use schema VEHICLE_SAFETY.GOLD")
    return conn


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False  # more than MAX_ROWS rows; only the first MAX_ROWS are kept


def _plain(value):
    """Snowflake values as JSON-friendly Python values."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def run_query(conn, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
    cur = conn.cursor()
    cur.execute(sql)
    columns = [c[0].lower() for c in cur.description]
    rows = [tuple(_plain(v) for v in row) for row in cur.fetchmany(max_rows + 1)]
    return QueryResult(sql, columns, rows[:max_rows], truncated=len(rows) > max_rows)
