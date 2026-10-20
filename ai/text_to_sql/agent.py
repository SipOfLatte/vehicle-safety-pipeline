"""Text-to-SQL: Claude looks up exact names, writes a query, runs it read-only, and answers from the result."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ai.text_to_sql.executor import MAX_ROWS, QueryResult, run_query
from ai.text_to_sql.guard import UnsafeSQL, check_sql

SQL_MODEL = "claude-sonnet-5-5"
EFFORT = "medium"  # the documented starting point for multistep tool use on Sonnet 5.5
MAX_TOKENS = 8000
MAX_MODEL_CALLS = 6  # caps cost per question; a typical question takes 2 or 3
ROWS_SHOWN_TO_MODEL = 30

SYSTEM = """You answer questions about US vehicle safety complaints and recalls (NHTSA data) by querying a Snowflake database. You can read only the four tables described below.

How to work:
- When the question names a vehicle, brand or system and you aren't sure of the exact stored value, call find_values first. Values are upper case and model families group variants (CAMRY includes CAMRY HYBRID).
- Write one Snowflake SELECT and run it with run_sql. If it fails or returns something unexpected, fix it and run it again.
- Then answer in one to four sentences, using only numbers from the query results. The app shows your final SQL and its full result table under your answer, so don't repeat the SQL or list every row.

Rules:
- Complaint totals are counts, not rates. NHTSA publishes no sales figures, and best-selling models get more complaints because more of them are on the road. Say so when you rank or compare vehicles by complaints. For "safest" or "most dangerous" questions, rank by severe_share among vehicles with at least 30 complaints, and say that is what you did.
- Complaints are unverified owner reports.
- Don't add up fct_complaints_monthly across component groups to get a vehicle's total; one complaint can sit in several groups. Use vehicle_complaint_summary for totals per vehicle.
- Your last successful run_sql is the result the user sees, so make it the query that answers the question, with readable column names and ordered sensibly.
- Return the rows the question asks for and no others. "Which X has the most" is one row; "which five" is five; a list question without a number gets every matching row, or the top 10 if there are many. Don't add rows the question didn't ask about (other component groups, other years); put useful context in extra columns instead. You can still mention runners-up in your answer if an earlier query showed them.
- If these tables can't answer the question (sales, prices, the text of individual complaints, anything outside vehicle safety), say so briefly without running a query.

Schema:

"""

TOOLS = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only Snowflake SELECT on the four tables and get back the column names, the "
            f"first {ROWS_SHOWN_TO_MODEL} rows and the row count. Unqualified table names resolve to the right "
            "schema. Errors come back as text you can act on."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_values",
        "description": (
            "Find the exact stored values for a make, model family or component group that contain some text, "
            "with complaint counts so the main match is obvious. Use it before filtering on a name you're unsure of."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "column": {"type": "string", "enum": ["make", "model_family", "component_group"]},
                "contains": {"type": "string", "description": "Case-insensitive text to look for, e.g. 'silverado'."},
                "make": {
                    "type": ["string", "null"],
                    "description": "Optional exact make to narrow a model_family search, e.g. 'CHEVROLET'.",
                },
            },
            "required": ["column", "contains", "make"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class Step:
    tool: str
    input: dict
    ok: bool
    summary: str


@dataclass
class AskResult:
    answer: str
    result: QueryResult | None  # the last query that ran successfully
    steps: list[Step] = field(default_factory=list)
    stop: str = "end_turn"  # end_turn, refusal, max_tokens or max_calls


def find_values(conn, column: str, contains: str, make: str | None) -> list[dict]:
    cur = conn.cursor()
    pattern = f"%{contains.strip()}%"
    if column == "component_group":
        cur.execute(
            "select component_group as value, sum(complaints) as complaints from recall_coverage "
            "where component_group ilike %s group by 1 order by 2 desc limit 20",
            (pattern,),
        )
    elif column == "make":
        cur.execute(
            "select make as value, sum(complaints) as complaints from vehicle_complaint_summary "
            "where make ilike %s group by 1 order by 2 desc limit 20",
            (pattern,),
        )
    else:
        cur.execute(
            "select make || ' / ' || model_family as value, sum(complaints) as complaints "
            "from vehicle_complaint_summary where model_family ilike %s and (%s is null or make = upper(%s)) "
            "group by 1 order by 2 desc limit 20",
            (pattern, make, make),
        )
    return [{"value": v, "complaints": int(n)} for v, n in cur.fetchall()]


def _run_tool(conn, name: str, args: dict, state: dict) -> tuple[str, bool, str]:
    """(content for the model, ok, one-line summary for the app)."""
    if name == "find_values":
        values = find_values(conn, args["column"], args["contains"], args.get("make"))
        return json.dumps(values), True, f"{len(values)} matches for '{args['contains']}'"
    if name != "run_sql":
        return f"Unknown tool {name}.", False, "unknown tool"
    try:
        sql = check_sql(args["sql"])
    except UnsafeSQL as e:
        return str(e), False, str(e)
    try:
        result = run_query(conn, sql)
    except Exception as e:  # Snowflake errors go back to the model so it can fix the query
        message = getattr(e, "msg", None) or str(e)
        return f"Snowflake error: {message}", False, f"error: {message[:160]}"
    state["result"] = result
    count = f"more than {MAX_ROWS}" if result.truncated else str(len(result.rows))
    payload = {"row_count": count, "columns": result.columns, "rows": result.rows[:ROWS_SHOWN_TO_MODEL]}
    return json.dumps(payload, default=str), True, f"{count} rows"


def ask(client, conn, question: str, schema: str, on_usage=None) -> AskResult:
    """Answer one question. conn must be an ask_connection() (read-only role, GOLD schema, timeout)."""
    messages = [{"role": "user", "content": question}]
    steps: list[Step] = []
    state: dict = {"result": None}
    for _ in range(MAX_MODEL_CALLS):
        response = client.messages.create(
            model=SQL_MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=SYSTEM + schema,
            tools=TOOLS,
            messages=messages,
            cache_control={"type": "ephemeral"},  # caches the system prompt, tools and turns so far
        )
        if on_usage:
            on_usage("sql", SQL_MODEL, response.usage)
        if response.stop_reason in ("refusal", "max_tokens"):
            text = "".join(b.text for b in response.content if b.type == "text")
            return AskResult(text, state["result"], steps, response.stop_reason)
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            return AskResult(text, state["result"], steps)
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            content, ok, summary = _run_tool(conn, block.name, block.input, state)
            steps.append(Step(block.name, dict(block.input), ok, summary))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": not ok})
        messages.append({"role": "user", "content": results})
    return AskResult("I couldn't finish this question within the step limit.", state["result"], steps, "max_calls")
