"""Run the text-to-SQL eval: execution accuracy against hand-written reference queries.

Usage (from the repo root, with ANTHROPIC_API_KEY set). Queries run as the app's read-only ASK_ROLE,
using the app's Snowflake credentials from .streamlit/secrets.toml:
    python -m ai.evals.run_sql_eval --set dev        # the 25 tuning questions (about USD 0.30)
    python -m ai.evals.run_sql_eval --set holdout    # the 15 held-out questions; run once, after tuning
    python -m ai.evals.run_sql_eval --ids s03 s17    # specific questions

Writes ai/evals/results/sql_<date>_<set>.json and prints a summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from ai.pricing import cost_usd
from ai.text_to_sql.agent import ask
from ai.text_to_sql.executor import ask_connection, run_query
from ai.text_to_sql.schema import schema_context

HERE = Path(__file__).parent
# How an answer says the tables can't answer the question.
DECLINES = r"can't|cannot|can not|unable to|don't (have|include|contain|cover)|do not (have|include|contain|cover)|doesn't (have|include)|no (sales|price|pricing|ownership)"
REPO = HERE.parents[1]


# --- result matching ---------------------------------------------------------------------------

def _close(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isclose(a, b, rel_tol=0.01, abs_tol=0.0005):
            return True
        # a share given as a percentage: 0.2188 vs 21.88
        return 0 <= abs(a) <= 1 and math.isclose(a * 100, b, rel_tol=0.01, abs_tol=0.05)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    return a == b


def _sort_key(value):
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, (int, float)):
        return (2, float(value))  # unrounded: rounding made 0.155 and 0.1515 sort as equals
    return (3, str(value).strip().upper())


def _column_matches(expected: list, actual: list) -> bool:
    return len(expected) == len(actual) and all(
        _close(e, a) for e, a in zip(sorted(expected, key=_sort_key), sorted(actual, key=_sort_key), strict=True)
    )


def results_match(expected_cols, expected_rows, actual_cols, actual_rows, ordered: bool = False) -> bool:
    """True if every expected column has a matching actual column (by values, not by name) and the
    rows agree on those columns. Extra actual columns are fine; extra or missing rows are not."""
    if len(expected_rows) != len(actual_rows):
        return False
    if not expected_rows:
        return True
    expected_by_col = [[r[i] for r in expected_rows] for i in range(len(expected_cols))]
    actual_by_col = [[r[j] for r in actual_rows] for j in range(len(actual_cols))]
    mapping, used = [], set()
    for values in expected_by_col:
        match = next((j for j, other in enumerate(actual_by_col) if j not in used and _column_matches(values, other)), None)
        if match is None:
            return False
        mapping.append(match)
        used.add(match)
    projected = [tuple(r[j] for j in mapping) for r in actual_rows]
    expected = [tuple(r) for r in expected_rows]
    if not ordered:
        projected = sorted(projected, key=lambda r: tuple(map(_sort_key, r)))
        expected = sorted(expected, key=lambda r: tuple(map(_sort_key, r)))
    return all(all(_close(e, a) for e, a in zip(er, ar, strict=True)) for er, ar in zip(expected, projected, strict=True))


# --- runner ------------------------------------------------------------------------------------

def load_app_secrets(path: Path) -> None:
    """Use the app's Snowflake service user (which holds ASK_ROLE), not the pipeline user."""
    for key, value in tomllib.loads(path.read_text()).items():
        if isinstance(value, str) and key.startswith(("SNOWFLAKE_", "SF_")):
            os.environ[key] = value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--ids", nargs="*", help="run only these question ids")
    parser.add_argument("--secrets", type=Path, default=REPO / ".streamlit" / "secrets.toml")
    args = parser.parse_args()

    import anthropic

    if args.secrets.exists():  # local runs; in CI the SNOWFLAKE_* variables come from Actions secrets
        load_app_secrets(args.secrets)
    client = anthropic.Anthropic(max_retries=3)
    questions = yaml.safe_load((HERE / "sql_questions.yaml").read_text())["questions"]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    elif args.set != "all":
        questions = [q for q in questions if q["set"] == args.set]

    rows = []
    with ask_connection() as conn:
        schema = schema_context(conn)
        for q in questions:
            usage = []
            started = time.monotonic()
            out = ask(client, conn, q["question"], schema, on_usage=lambda p, m, u, usage=usage: usage.append(cost_usd(m, u)))
            seconds = round(time.monotonic() - started, 1)
            row = {
                "id": q["id"], "set": q["set"], "question": q["question"], "reviewed": q.get("reviewed", False),
                "answer": out.answer, "stop": out.stop, "sql": out.result.sql if out.result else None,
                "steps": [f"{s.tool}: {s.summary}" for s in out.steps],
                "model_calls": len(usage), "cost_usd": round(sum(usage), 5), "seconds": seconds,
            }
            if q.get("answerable") is False:
                row["passed"] = out.result is None and bool(re.search(DECLINES, out.answer, re.IGNORECASE))
            else:
                references = [run_query(conn, sql) for sql in [q["sql"], *q.get("alternatives", [])]]
                row["expected_rows"] = len(references[0].rows)
                row["passed"] = out.result is not None and any(
                    results_match(ref.columns, ref.rows, out.result.columns, out.result.rows, q.get("ordered", False))
                    for ref in references
                )
                if out.result is not None:
                    row["actual_rows"] = len(out.result.rows)
            rows.append(row)
            print(f"{q['id']} {'PASS' if row['passed'] else 'FAIL'} calls={row['model_calls']} "
                  f"${row['cost_usd']:.4f} {seconds}s  {q['question'][:70]}")

    summary = {}
    for name in sorted({r["set"] for r in rows}):
        group = [r for r in rows if r["set"] == name]
        summary[name] = {
            "questions": len(group),
            "passed": sum(r["passed"] for r in group),
            "execution_accuracy": round(sum(r["passed"] for r in group) / len(group), 3),
            "reviewed": sum(r["reviewed"] for r in group),
            "cost_usd": round(sum(r["cost_usd"] for r in group), 4),
            "avg_model_calls": round(sum(r["model_calls"] for r in group) / len(group), 2),
            "stops": dict(Counter(r["stop"] for r in group)),
        }
    label = "ids" if args.ids else args.set
    out_path = HERE / "results" / f"sql_{dt.date.today().isoformat()}_{label}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "results": rows}, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
