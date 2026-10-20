"""Checks model-written SQL before it runs. The read-only ASK_ROLE is the real boundary; this catches
mistakes early with a message the model can act on. The SQL itself runs unchanged (re-generating it
through sqlglot can change its meaning), with the session schema set to GOLD."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = ("fct_complaints_monthly", "vehicle_complaint_summary", "recall_coverage", "component_trends")
DATABASE, SCHEMA = "VEHICLE_SAFETY", "GOLD"
# Prefixes of function names that are never needed for these tables. AI_ covers the Cortex AI functions
# (AI_COMPLETE etc.), which cost money; schema-qualified calls such as SNOWFLAKE.CORTEX.COMPLETE are
# blocked separately below.
BLOCKED_FUNCTIONS = ("RESULT_SCAN", "SYSTEM$", "GET_DDL", "AI_")


class UnsafeSQL(ValueError):
    pass


def check_sql(sql: str) -> str:
    """The query without a trailing semicolon, if it's a single read-only SELECT on the allowed tables.
    Raises UnsafeSQL with a reason otherwise."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"Could not parse the SQL: {e}") from None
    if len(statements) != 1:
        raise UnsafeSQL("Send exactly one SQL statement.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeSQL("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into)):
            raise UnsafeSQL("Only SELECT queries are allowed.")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise UnsafeSQL(f"Schema-qualified functions such as {node.sql(dialect='snowflake')[:60]} are not allowed.")
        if isinstance(node, exp.Func):
            name = (node.sql_name() if not isinstance(node, exp.Anonymous) else node.name).upper()
            if name.startswith(BLOCKED_FUNCTIONS):
                raise UnsafeSQL(f"Function {name} is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:  # table functions such as TABLE(FLATTEN(...))
            continue
        if name in cte_names and not table.db:
            continue
        if name not in ALLOWED_TABLES:
            raise UnsafeSQL(f"Table {table.name} is not available. Use only: {', '.join(ALLOWED_TABLES)}.")
        if table.db and table.db.upper() != SCHEMA or table.catalog and table.catalog.upper() != DATABASE:
            raise UnsafeSQL(f"Use the {SCHEMA} tables only: {', '.join(ALLOWED_TABLES)}.")
    return sql.strip().rstrip(";").strip()
