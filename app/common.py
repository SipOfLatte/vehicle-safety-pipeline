"""Shared setup for the app pages: secrets, connections, spend limits."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for `import ai`

MAX_QUESTIONS_PER_SESSION = 10
DAILY_BUDGET_USD = 0.50
# Claude Haiku 5.5, USD per million tokens (input, output).
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}


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


@st.cache_data(ttl=3600)
def data_as_of() -> str:
    cur = snowflake().cursor()
    cur.execute("select max(loaded_at)::date from VEHICLE_SAFETY.BRONZE.LOAD_LOG")
    return str(cur.fetchone()[0])


def spent_today_usd() -> float:
    cur = snowflake().cursor()
    cur.execute("select coalesce(sum(cost_usd), 0) from VEHICLE_SAFETY.AI.LLM_USAGE where called_at >= current_date()")
    return float(cur.fetchone()[0])


def record_usage(purpose: str, model: str, usage) -> None:
    price_in, price_out = PRICES[model]
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
    snowflake().cursor().execute(
        "insert into VEHICLE_SAFETY.AI.LLM_USAGE (purpose, model, input_tokens, output_tokens, cost_usd) "
        "values (%s, %s, %s, %s, %s)",
        (purpose, model, usage.input_tokens, usage.output_tokens, cost),
    )


def check_limits() -> str | None:
    """A message explaining why the user can't ask now, or None if they can."""
    if st.session_state.get("questions_asked", 0) >= MAX_QUESTIONS_PER_SESSION:
        return f"This demo allows {MAX_QUESTIONS_PER_SESSION} questions per session. Reload the page to start a new one."
    if spent_today_usd() >= DAILY_BUDGET_USD:
        return "The demo's daily AI budget is used up. It resets at midnight (US Pacific time)."
    return None
