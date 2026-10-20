"""Ask page: Claude writes SQL against the GOLD summary tables, runs it read-only, and answers from the result."""

import pandas as pd
import streamlit as st

from common import ask_snowflake, check_limits, claude, data_as_of, load_secrets, record_usage, sql_schema

load_secrets()

from ai.text_to_sql.agent import ask  # noqa: E402
from ai.text_to_sql.executor import MAX_ROWS  # noqa: E402

st.set_page_config(page_title="Ask the data", page_icon="📊", layout="wide")
st.title("Ask the data")
st.caption(
    f"Claude writes SQL against four summary tables of NHTSA complaints and recalls, runs it with a "
    f"read-only role, and answers from the result. Data as of {data_as_of()}. Complaint totals are counts, "
    f"not rates: NHTSA publishes no sales figures, so popular models get more complaints."
)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def answer(question: str):
    """The answer, final query and result for a question. Cached for a day, since the data changes daily."""
    return ask(claude(), ask_snowflake(), question, sql_schema(), on_usage=record_usage)


def chart(df: pd.DataFrame) -> None:
    """A line chart for a time series, a bar chart for a short ranking, nothing otherwise."""
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in ("model_year", "year")]
    if len(df) < 2 or len(numeric) != 1:
        return
    time_col = next((c for c in df.columns if c in ("month", "year", "model_year")), None)
    if time_col and len(df.columns) == 2:
        if time_col == "month":  # ISO date strings; a real date axis spaces the months correctly
            df = df.assign(month=pd.to_datetime(df["month"]))
        st.line_chart(df, x=time_col, y=numeric[0])
        return
    labels = [c for c in df.columns if c not in numeric]
    if labels and len(df) <= 30:
        df = df.assign(label=df[labels].astype(str).agg(" ".join, axis=1))
        st.bar_chart(df, x="label", y=numeric[0], horizontal=True, sort=f"-{numeric[0]}")


def show(out) -> None:
    st.markdown(out.answer or "_No answer._")
    if out.stop == "max_calls":
        st.info("Claude hit the step limit for one question. Try asking more specifically.")
    if out.result is None:
        return
    df = pd.DataFrame(out.result.rows, columns=out.result.columns)
    chart(df)
    st.dataframe(df, hide_index=True, width="stretch")
    if out.result.truncated:
        st.caption(f"Showing the first {MAX_ROWS} rows.")
    with st.expander("SQL"):
        st.code(out.result.sql, language="sql")
    with st.expander(f"How Claude got there ({len(out.steps)} step{'' if len(out.steps) == 1 else 's'})"):
        for s in out.steps:
            detail = s.input.get("sql") or f"{s.input.get('column')} containing '{s.input.get('contains')}'"
            st.markdown(f"{'✅' if s.ok else '⚠️'} **{s.tool}**: {s.summary}")
            st.code(detail, language="sql" if s.tool == "run_sql" else None)


for item in st.session_state.get("ask_history", []):
    with st.chat_message("user"):
        st.markdown(item["question"])
    with st.chat_message("assistant"):
        show(item["out"])

examples = [
    "Which 10 makes have the most complaints?",
    "Among 2022 vehicles with at least 30 complaints, which have the highest share of severe complaints?",
    "Which 2020 vehicles have the biggest complaint clusters with no recall?",
]
st.caption("Try: " + " · ".join(f"*{e}*" for e in examples))

question = st.chat_input("Ask a question about complaint and recall numbers")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    blocked = check_limits("ask")
    with st.chat_message("assistant"):
        if blocked:
            st.info(blocked)
        else:
            st.session_state["questions_asked"] = st.session_state.get("questions_asked", 0) + 1
            with st.spinner("Writing and running SQL…"):
                out = answer(question)
            show(out)
            st.session_state.setdefault("ask_history", []).append({"question": question, "out": out})
