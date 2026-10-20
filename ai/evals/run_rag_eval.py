"""Run the RAG retrieval eval: hit rate in the top 5, with and without metadata filters.

Usage (from the repo root, with SNOWFLAKE_* and ANTHROPIC_API_KEY set):
    python -m ai.evals.run_rag_eval            # retrieval only (about USD 0.01 of Claude calls)
    python -m ai.evals.run_rag_eval --answers  # also generate answers for spot-checking citations

Writes ai/evals/results/rag_<date>.json and prints a summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import yaml

from ai.rag.answer import cited_ids, stream_answer
from ai.rag.retrieval import Filters, extract_filters, load_vocabulary, search, validate_filters
from ai.snowflake_conn import connect

HERE = Path(__file__).parent
K = 5


def is_hit(kind: str, relevant, doc) -> bool:
    if kind == "recall":
        return doc.doc_id == relevant
    return (
        doc.doc_type == "complaint"
        and relevant["make"] in doc.makes
        and str(relevant["model_family"]) in doc.model_families
        and relevant["model_year"] in doc.model_years
        and relevant["component_group"] in doc.component_groups
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", action="store_true", help="also generate answers and check their citations")
    args = parser.parse_args()

    import anthropic

    client = anthropic.Anthropic()
    questions = yaml.safe_load((HERE / "rag_questions.yaml").read_text())
    results = []
    with connect() as conn:
        vocab, groups = load_vocabulary(conn)
        for kind in ("recall", "complaint"):
            for q in questions[kind]:
                filters = validate_filters(extract_filters(client, q["question"], groups), vocab)
                filtered = search(conn, q["question"], filters, k=K)
                unfiltered = search(conn, q["question"], Filters(), k=K)
                row = {
                    "id": q["id"], "kind": kind, "question": q["question"], "reviewed": q.get("reviewed", False),
                    "filters": filters.describe(), "dropped_filters": filters.dropped,
                    "hit": any(is_hit(kind, q["relevant"], d) for d in filtered),
                    "hit_without_filters": any(is_hit(kind, q["relevant"], d) for d in unfiltered),
                    "relevant_in_top5": sum(is_hit(kind, q["relevant"], d) for d in filtered),
                    "top5": [{"doc_id": d.doc_id, "score": round(d.score, 4)} for d in filtered],
                }
                if args.answers and filtered:
                    answer = "".join(stream_answer(client, q["question"], filtered))
                    real, invented = cited_ids(answer, filtered)
                    row |= {"answer": answer, "cited": real, "invented_citations": invented}
                results.append(row)
                print(f"{q['id']} hit={row['hit']!s:5} no_filter_hit={row['hit_without_filters']!s:5} {row['filters']}")

    summary = {}
    for kind in ("recall", "complaint"):
        rows = [r for r in results if r["kind"] == kind]
        summary[kind] = {
            "questions": len(rows),
            "hit_rate_at_5": sum(r["hit"] for r in rows) / len(rows),
            "hit_rate_at_5_without_filters": sum(r["hit_without_filters"] for r in rows) / len(rows),
            "reviewed": sum(r["reviewed"] for r in rows),
        }
    if args.answers:
        answered = [r for r in results if "answer" in r]
        summary["answers_with_invented_citations"] = sum(bool(r["invented_citations"]) for r in answered)
        summary["answers_without_citations"] = sum(not r["cited"] for r in answered)

    out = HERE / "results" / f"rag_{dt.date.today().isoformat()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
