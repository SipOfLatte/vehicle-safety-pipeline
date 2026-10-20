"""The schema description the model writes SQL against: the four GOLD analytics tables, with column
types from Snowflake and descriptions from the dbt docs YAML (dbt/models/gold/_gold.yml)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai.text_to_sql.guard import ALLOWED_TABLES

GOLD_YAML = Path(__file__).resolve().parents[2] / "dbt" / "models" / "gold" / "_gold.yml"


def column_types(conn) -> dict[str, dict[str, str]]:
    """table -> column -> Snowflake data type, for the allowed tables."""
    cur = conn.cursor()
    tables = ", ".join(f"'{t.upper()}'" for t in ALLOWED_TABLES)  # constants, not user input
    cur.execute(
        f"""select lower(table_name), lower(column_name), data_type
            from VEHICLE_SAFETY.INFORMATION_SCHEMA.COLUMNS
            where table_schema = 'GOLD' and table_name in ({tables})
            order by table_name, ordinal_position"""
    )
    types: dict[str, dict[str, str]] = {}
    for table, column, data_type in cur.fetchall():
        types.setdefault(table, {})[column] = data_type
    return types


def component_groups(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute("select distinct component_group from VEHICLE_SAFETY.GOLD.RECALL_COVERAGE order by 1")
    return [r[0] for r in cur.fetchall()]


def data_range(conn) -> tuple[str, str]:
    """First and last complaint month in the data, as YYYY-MM-DD."""
    cur = conn.cursor()
    cur.execute("select min(month)::string, max(month)::string from VEHICLE_SAFETY.GOLD.FCT_COMPLAINTS_MONTHLY")
    return cur.fetchone()


def render(types: dict[str, dict[str, str]], groups: list[str], months: tuple[str, str]) -> str:
    """Plain-text schema for the system prompt. Columns missing from the YAML still appear, with their type."""
    docs = {m["name"]: m for m in yaml.safe_load(GOLD_YAML.read_text())["models"]}
    parts = []
    for table in ALLOWED_TABLES:
        model = docs[table]
        described = {c["name"]: " ".join(c.get("description", "").split()) for c in model.get("columns", [])}
        lines = [f"TABLE {table}", "  " + " ".join(model["description"].split())]
        for column, data_type in types.get(table, {}).items():
            note = described.get(column, "")
            lines.append(f"  - {column} {data_type}" + (f": {note}" if note else ""))
        parts.append("\n".join(lines))
    parts.append("Component groups (exact values): " + ", ".join(groups))
    parts.append(f"Complaint months in the data: {months[0]} to {months[1]}.")
    return "\n\n".join(parts)


def schema_context(conn) -> str:
    return render(column_types(conn), component_groups(conn), data_range(conn))
