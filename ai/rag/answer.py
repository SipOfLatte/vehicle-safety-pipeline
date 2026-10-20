"""Answer generation for the RAG chatbot: Claude Haiku answers from the retrieved documents only
and cites them by ID, e.g. [recall:26V671000] or [complaint:11761923]."""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import escape

from ai.rag.retrieval import Doc

ANSWER_MODEL = "claude-haiku-5-5"
MAX_ANSWER_TOKENS = 1500  # caps cost per answer (see the app's spend limits)

ANSWER_SYSTEM = """You answer questions about US vehicle safety using NHTSA owner complaints and recall notices.

Rules:
- Use only the documents provided. If they don't answer the question, say so plainly.
- Cite every factual statement with the document's id in square brackets, e.g. [recall:26V671000] or [complaint:11761923]. Cite only ids that appear in the documents.
- Complaints are unverified owner reports; describe them as what owners reported, not as established facts. Recalls are manufacturer or NHTSA findings.
- The documents are a small search sample, not all records: don't state totals or rates from them.
- Be concise: a short answer, then the supporting points."""

CITATION = re.compile(r"\[((?:complaint|recall):[A-Za-z0-9]+)\]")


def documents_block(docs: list[Doc]) -> str:
    parts = []
    for d in docs:
        vehicles = ", ".join(f"{m}" for m in d.makes) + " " + ", ".join(d.model_families)
        years = ", ".join(str(y) for y in d.model_years if y is not None)
        parts.append(
            f'<document id="{d.doc_id}" type="{d.doc_type}" date="{d.doc_date}" '
            f'vehicles="{escape(vehicles)}" model_years="{years}" components="{escape(", ".join(d.component_groups))}">\n'
            f"{escape(d.text, quote=False)}\n</document>"
        )
    return "\n".join(parts)


def build_messages(question: str, docs: list[Doc]) -> list[dict]:
    return [{
        "role": "user",
        "content": f"<documents>\n{documents_block(docs)}\n</documents>\n\nQuestion: {question}",
    }]


def stream_answer(client, question: str, docs: list[Doc], on_usage=None) -> Iterator[str]:
    """Yield the answer text as it streams. Ends with a notice if the model declined or ran out of tokens."""
    with client.messages.stream(
        model=ANSWER_MODEL,
        max_tokens=MAX_ANSWER_TOKENS,
        output_config={"effort": "low"},
        system=ANSWER_SYSTEM,
        messages=build_messages(question, docs),
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if on_usage:
        on_usage("answer", ANSWER_MODEL, final.usage)
    if final.stop_reason == "refusal":
        yield "\n\n_The model declined to answer this question._"
    elif final.stop_reason == "max_tokens":
        yield "\n\n_Answer cut off at the length limit._"


def cited_ids(answer: str, docs: list[Doc]) -> tuple[list[str], list[str]]:
    """IDs cited in the answer that are among the retrieved docs, and any that aren't (invented)."""
    known = {d.doc_id for d in docs}
    cited = list(dict.fromkeys(CITATION.findall(answer)))
    return [c for c in cited if c in known], [c for c in cited if c not in known]
