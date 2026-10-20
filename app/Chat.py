"""Chat page: ask about vehicle safety complaints and recalls; answers cite NHTSA records."""

import streamlit as st

from common import check_limits, claude, data_as_of, load_secrets, record_usage, show_freshness, snowflake

load_secrets()

from ai.rag.answer import cited_ids, stream_answer  # noqa: E402
from ai.rag.retrieval import extract_filters, load_vocabulary, search, validate_filters  # noqa: E402

st.set_page_config(page_title="Vehicle safety chat", page_icon="🚗", layout="centered")
st.title("Vehicle safety chat")
show_freshness()
st.caption(
    f"Searches NHTSA owner complaints (since 2020) and vehicle recalls (since 2010). "
    f"Data as of {data_as_of()}. Complaints are unverified owner reports."
)


@st.cache_data(ttl=24 * 3600)
def vocabulary():
    vocab, groups = load_vocabulary(snowflake())
    return {k: sorted(v) for k, v in vocab.items()}, groups


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def retrieve(question: str):
    """Filters and top documents for a question. Cached for a day, since the data changes daily."""
    vocab, groups = vocabulary()
    filters = extract_filters(claude(), question, groups, on_usage=record_usage)
    filters = validate_filters(filters, {k: set(v) for k, v in vocab.items()})
    docs = search(snowflake(), question, filters, k=5)
    return filters, docs


def show_sources(docs, answer):
    real, invented = cited_ids(answer, docs)
    if invented:
        st.warning(f"The answer cited records it wasn't given: {', '.join(invented)}. Treat those parts with caution.")
    with st.expander(f"Sources ({len(docs)} retrieved, {len(real)} cited)"):
        for d in docs:
            mark = "✅ cited" if d.doc_id in real else "not cited"
            st.markdown(
                f"**[{d.doc_id}]({d.url})** · {d.doc_date} · {', '.join(d.makes)} {', '.join(d.model_families)} "
                f"· similarity {d.score:.2f} · {mark}"
            )
            st.caption(d.text[:600] + ("…" if len(d.text) > 600 else ""))


for msg in st.session_state.get("history", []):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

examples = [
    "Are there airbag recalls on the 2020 Honda CR-V?",
    "What do owners report about engine problems on the 2018 Ford Escape?",
    "Can the suspension break on a 2021 Tesla Model Y?",
]
st.caption("Try: " + " · ".join(f"*{e}*" for e in examples))

question = st.chat_input("Ask about a vehicle's complaints or recalls")
if question:
    with st.chat_message("user"):
        st.markdown(question)
    blocked = check_limits("chat")
    with st.chat_message("assistant"):
        if blocked:
            st.info(blocked)
        else:
            st.session_state["questions_asked"] = st.session_state.get("questions_asked", 0) + 1
            with st.spinner("Searching complaints and recalls…"):
                filters, docs = retrieve(question)
            note = f"Filters: {filters.describe()}"
            if filters.dropped:
                note += f" (ignored {', '.join(filters.dropped)}: not found in the data)"
            st.caption(note)
            if not docs:
                answer = "I couldn't find any matching complaints or recalls. Try a broader question."
                st.markdown(answer)
            else:
                answer = st.write_stream(stream_answer(claude(), question, docs, on_usage=record_usage))
                show_sources(docs, answer)
            st.session_state.setdefault("history", []).extend(
                [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
            )
