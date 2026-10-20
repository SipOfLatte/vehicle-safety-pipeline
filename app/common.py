"""Shared setup for the app pages: secrets, connections, spend limits."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `import ai`

MAX_QUESTIONS_PER_SESSION = 10
# Claude spend per day, USD 0.50 in total, split so one page can't use up the other's share.
# LLM_USAGE.purpose: the chatbot logs 'filters' and 'answer', the Ask page logs 'sql'.
DAILY_BUDGETS_USD = {"chat": 0.20, "ask": 0.30}
PURPOSES = {"chat": ("filters", "answer"), "ask": ("sql",)}


def load_secrets() -> None:
    """Copy Streamlit secrets into the environment (Streamlit Cloud); local runs use the shell env."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str):
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


@st.cache_resource
def snowflake():
    from ai.snowflake_conn import connect

    return connect(role="APP_ROLE", warehouse="APP_WH")


@st.cache_resource
def claude():
    import anthropic

    return anthropic.Anthropic(max_retries=2, timeout=60.0)


# The pipeline runs daily at 12:00 UTC; more than this since the last load means a run was missed.
STALE_AFTER_HOURS = 36


@st.cache_data(ttl=600)
def last_load() -> tuple[str, float]:
    """When the latest NHTSA load finished (UTC, as text) and how many hours ago that was."""
    cur = snowflake().cursor()
    # Snowflake's session time zone defaults to US Pacific; report in UTC.
    cur.execute(
        "select to_char(convert_timezone('UTC', max(loaded_at)), 'YYYY-MM-DD HH24:MI'), "
        "datediff('minute', max(loaded_at), current_timestamp()) / 60 from VEHICLE_SAFETY.BRONZE.LOAD_LOG"
    )
    loaded, hours = cur.fetchone()
    return loaded, float(hours)


def data_as_of() -> str:
    return last_load()[0][:10]


def show_freshness() -> None:
    """Sidebar line saying how fresh the data is, with a warning if the daily update is late."""
    loaded, hours = last_load()
    age = f"{hours:.0f} hours ago" if hours >= 1 else "less than an hour ago"
    st.sidebar.caption(f"NHTSA data loaded {loaded} UTC ({age}). The pipeline updates it daily at 12:00 UTC.")
    if hours > STALE_AFTER_HOURS:
        st.sidebar.warning("The daily update is late, so the newest complaints and recalls may be missing.")


def spent_today_usd(page: str) -> float:
    cur = snowflake().cursor()
    purposes = PURPOSES[page]
    cur.execute(
        "select coalesce(sum(cost_usd), 0) from VEHICLE_SAFETY.AI.LLM_USAGE "
        f"where called_at >= current_date() and purpose in ({', '.join(['%s'] * len(purposes))})",
        purposes,
    )
    return float(cur.fetchone()[0])


def record_usage(purpose: str, model: str, usage) -> None:
    from ai.pricing import cost_usd

    cost = cost_usd(model, usage)
    snowflake().cursor().execute(
        "insert into VEHICLE_SAFETY.AI.LLM_USAGE (purpose, model, input_tokens, output_tokens, cost_usd) "
        "values (%s, %s, %s, %s, %s)",
        (purpose, model, usage.input_tokens, usage.output_tokens, cost),
    )


def check_limits(page: str) -> str | None:
    """A message explaining why the user can't ask now, or None if they can."""
    if st.session_state.get("questions_asked", 0) >= MAX_QUESTIONS_PER_SESSION:
        return f"This demo allows {MAX_QUESTIONS_PER_SESSION} questions per session. Reload the page to start a new one."
    if spent_today_usd(page) >= DAILY_BUDGETS_USD[page]:
        return "This page's daily AI budget is used up. It resets at midnight (US Pacific time)."
    return None


@st.cache_resource
def ask_snowflake():
    """Separate connection for model-written SQL: read-only ASK_ROLE, GOLD schema, 30-second timeout."""
    from ai.text_to_sql.executor import ask_connection

    return ask_connection()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def sql_schema() -> str:
    from ai.text_to_sql.schema import schema_context

    return schema_context(ask_snowflake())
