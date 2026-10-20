"""Load the latest NHTSA files from the S3 stage into Snowflake bronze.

Same approach as snowflake/04_load_bronze.sql: bronze holds only the latest snapshot, and
each table is emptied and reloaded in one transaction, so a failed COPY leaves the previous
data in place. Every load is recorded in BRONZE.LOAD_LOG for the row-count health check.
"""

from __future__ import annotations

import os

TABLES = {
    # table: (datasets loaded into it, number of columns in the file)
    "COMPLAINTS": (["complaints"], 51),
    "RECALLS": (["recalls_post_2010", "recalls_pre_2010"], 29),
}
STAGE = "@VEHICLE_SAFETY.BRONZE.NHTSA_STAGE"
STAGE_PREFIX = "bronze/"


def connect():
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ.get("SNOWFLAKE_USER", "DBT_PIPELINE"),
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
        role="PIPELINE_ROLE",
        warehouse="PIPELINE_WH",
        database="VEHICLE_SAFETY",
        schema="BRONZE",
    )


def copy_sql(table: str, n_cols: int, s3_keys: list[str]) -> str:
    """COPY statement for exactly these files. Keys are S3 keys like bronze/complaints/load_date=.../X.txt.gz."""
    files = []
    for key in s3_keys:
        if not key.startswith(STAGE_PREFIX):
            raise ValueError(f"{key} is outside the stage prefix {STAGE_PREFIX}")
        files.append("'" + key[len(STAGE_PREFIX):].replace("'", "") + "'")
    cols = ", ".join(f"${i}" for i in range(1, n_cols + 1))
    return (
        f"COPY INTO {table} FROM (SELECT {cols}, METADATA$FILENAME, CURRENT_TIMESTAMP() FROM {STAGE}) "
        f"FILES = ({', '.join(files)}) FORCE = TRUE ON_ERROR = ABORT_STATEMENT"
    )


def load(conn, latest_keys: dict[str, str]) -> dict[str, int]:
    """Reload every bronze table from the latest file of each of its datasets. Returns rows loaded per table."""
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS LOAD_LOG ("
        "TABLE_NAME VARCHAR, S3_KEYS ARRAY, ROWS_LOADED NUMBER, LOADED_AT TIMESTAMP_LTZ)"
    )
    loaded = {}
    for table, (datasets, n_cols) in TABLES.items():
        keys = [latest_keys[d] for d in datasets]
        cur.execute("BEGIN")
        cur.execute(f"DELETE FROM {table}")
        cur.execute(copy_sql(table, n_cols, keys))
        # COPY returns one row per file; column 3 (0-based) is rows_loaded.
        rows = sum(int(r[3]) for r in cur.fetchall())
        cur.execute("COMMIT")
        cur.execute(
            "INSERT INTO LOAD_LOG SELECT %s, PARSE_JSON(%s), %s, CURRENT_TIMESTAMP()",
            (table, "[" + ", ".join(f'"{k}"' for k in keys) + "]", rows),
        )
        loaded[table] = rows
    return loaded


def check_row_counts(conn, max_drop: float = 0.01) -> dict[str, tuple[int, int]]:
    """Fail if any table's latest load has more than max_drop fewer rows than the load before it."""
    cur = conn.cursor()
    cur.execute(
        "SELECT TABLE_NAME, ROWS_LOADED FROM LOAD_LOG "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY TABLE_NAME ORDER BY LOADED_AT DESC) <= 2 "
        "ORDER BY TABLE_NAME, LOADED_AT DESC"
    )
    history: dict[str, list[int]] = {}
    for table, rows in cur.fetchall():
        history.setdefault(table, []).append(int(rows))
    return compare_counts(history, max_drop)


def compare_counts(history: dict[str, list[int]], max_drop: float) -> dict[str, tuple[int, int]]:
    """history: table -> [latest, previous]. Raises if latest dropped more than max_drop."""
    result, problems = {}, []
    for table, counts in history.items():
        if len(counts) < 2:
            continue
        latest, previous = counts[0], counts[1]
        result[table] = (previous, latest)
        if latest < previous * (1 - max_drop):
            problems.append(f"{table}: {previous:,} -> {latest:,} rows")
    if problems:
        raise RuntimeError("Row count dropped more than {:.0%}: {}".format(max_drop, "; ".join(problems)))
    return result
