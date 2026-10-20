"""Retrieval for the RAG chatbot: question -> metadata filters -> vector search in Snowflake.

Filters are extracted by Claude as structured output, then checked against the values that
actually exist in the data, so a misread make or model drops the filter instead of silently
returning nothing. The search itself is plain SQL: filter on the metadata arrays first,
then rank by VECTOR_COSINE_SIMILARITY against the question's Cortex embedding.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

FILTER_MODEL = "claude-haiku-5-5"
EMBEDDING_MODEL = "snowflake-arctic-embed-l-v2.0"
# arctic-embed v2 is trained with this prefix on queries (documents are embedded without one).
QUERY_PREFIX = "query: "
TABLE = "VEHICLE_SAFETY.AI.DOC_EMBEDDINGS"


@dataclass
class Filters:
    make: str | None = None
    model_family: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    component_group: str | None = None
    doc_type: str | None = None  # 'complaint', 'recall' or None for both
    family_variants: list[str] = field(default_factory=list)  # electric/hybrid families searched with model_family
    dropped: list[str] = field(default_factory=list)  # filters removed because no data matched

    def describe(self) -> str:
        parts = [p for p in (self.make, self.model_family) if p]
        if self.family_variants:
            parts.append("also " + " / ".join(self.family_variants))
        if self.year_from and self.year_to and self.year_from != self.year_to:
            parts.append(f"{self.year_from}-{self.year_to}")
        elif self.year_from:
            parts.append(str(self.year_from))
        if self.component_group:
            parts.append(self.component_group)
        if self.doc_type:
            parts.append(f"{self.doc_type}s only")
        return ", ".join(parts) or "no filters"


def filter_schema(component_groups: list[str]) -> dict:
    nullable_str = {"type": ["string", "null"]}
    nullable_int = {"type": ["integer", "null"]}
    return {
        "type": "object",
        "properties": {
            "make": nullable_str,
            "model_family": nullable_str,
            "year_from": nullable_int,
            "year_to": nullable_int,
            "component_group": {"anyOf": [{"type": "string", "enum": component_groups}, {"type": "null"}]},
            "doc_type": {"anyOf": [{"type": "string", "enum": ["complaint", "recall"]}, {"type": "null"}]},
        },
        "required": ["make", "model_family", "year_from", "year_to", "component_group", "doc_type"],
        "additionalProperties": False,
    }


FILTER_SYSTEM = """You turn a question about US vehicle safety complaints and recalls into search filters.
Return only what the question states or clearly implies; use null for anything not mentioned.
- make: manufacturer brand in upper case, e.g. TOYOTA, FORD, MERCEDES-BENZ. Infer it from a model name only when unambiguous (Model Y -> TESLA).
- model_family: model name in upper case without trim or powertrain words, e.g. CAMRY (not CAMRY HYBRID), F-150, MODEL Y, CR-V.
- year_from / year_to: model year range. One year -> both the same.
- component_group: the closest group from the allowed list, only if the question is about a specific system.
- doc_type: "recall" if the question asks only about recalls, "complaint" if only about owner complaints, else null."""


def extract_filters(client, question: str, component_groups: list[str], on_usage=None) -> Filters:
    response = client.messages.create(
        model=FILTER_MODEL,
        max_tokens=1024,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": filter_schema(component_groups)}},
        system=FILTER_SYSTEM,
        messages=[{"role": "user", "content": question}],
    )
    if on_usage:
        on_usage("filters", FILTER_MODEL, response.usage)
    if response.stop_reason == "refusal":
        return Filters()
    text = next(b.text for b in response.content if b.type == "text")
    return Filters(**json.loads(text))


def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# NHTSA files some electric and hybrid versions as their own model family (CHARGER EV, KONA ELECTRIC,
# RAV4 PRIME, WRANGLER 4XE), while the filter prompt strips powertrain words. Without this, "the electric
# Charger" filtered to CHARGER and excluded the only relevant recall (eval question h02).
POWERTRAIN_WORDS = {"EV", "BEV", "PHEV", "ELECTRIC", "(ELECTRIC)", "HYBRID", "E-HYBRID", "ACTIVEHYBRID", "PRIME", "ENERGI", "4XE"}


def family_variants(family: str, families: set[str]) -> list[str]:
    """Families named like `family` plus trailing words that end in a powertrain word, e.g. CHARGER -> CHARGER EV."""
    return sorted(
        f for f in families
        if f.startswith(family + " ") and f.split()[-1] in POWERTRAIN_WORDS
    )


def validate_filters(f: Filters, vocabulary: dict[str, set[str]]) -> Filters:
    """Keep only filter values that exist in the data. vocabulary: make -> set of model families."""
    if f.make:
        by_key = {_alnum(m): m for m in vocabulary}
        make = by_key.get(_alnum(f.make))
        if make is None:
            f.dropped.append(f"make {f.make}")
            f.make = f.model_family = None
        else:
            f.make = make
    if f.model_family:
        families = vocabulary.get(f.make, set()) if f.make else set().union(*vocabulary.values())
        by_key = {_alnum(m): m for m in families}
        family = by_key.get(_alnum(f.model_family))
        if family is None:
            f.dropped.append(f"model {f.model_family}")
            f.model_family = None
        else:
            f.model_family = family
            f.family_variants = family_variants(family, families)
    if f.year_from and not f.year_to:
        f.year_to = f.year_from
    if f.year_to and not f.year_from:
        f.year_from = f.year_to
    if f.year_from and f.year_to and f.year_from > f.year_to:
        f.year_from, f.year_to = f.year_to, f.year_from
    return f


def search_sql(f: Filters) -> tuple[str, dict]:
    """The vector search query and its bind parameters."""
    where, params = [], {}
    if f.make:
        where.append("array_contains(%(make)s::variant, makes)")
        params["make"] = f.make
    if f.model_family:
        where.append("arrays_overlap(model_families, parse_json(%(model_families)s))")
        params["model_families"] = json.dumps([f.model_family, *f.family_variants])
    if f.year_from and f.year_to:
        where.append("arrays_overlap(model_years, array_generate_range(%(year_from)s, %(year_to)s + 1))")
        params["year_from"], params["year_to"] = f.year_from, f.year_to
    # component_group is shown to the user but not used as a filter: it's the model's guess at
    # NHTSA's taxonomy (backup cameras sit under back-over prevention, axle shafts under power train),
    # and a wrong guess excluded the right recall in 2 of 10 eval questions. The vector search
    # handles the topic; filters stay on facts stated in the question.
    if f.doc_type:
        where.append("doc_type = %(doc_type)s")
        params["doc_type"] = f.doc_type
    # The question vector arrives as a bound constant. Embedding it inside this query (even in a CTE)
    # let Snowflake inline the Cortex call into the sort, running it once per row: about 441k calls
    # per unfiltered search. embed_question() runs it exactly once instead.
    sql = f"""
        select doc_id, doc_type, source_id, doc_date, makes, model_families, model_years,
               component_groups, is_severe, text,
               vector_cosine_similarity(embedding, parse_json(%(question_vector)s)::array::vector(float, 1024)) as score
        from {TABLE}
        {"where " + " and ".join(where) if where else ""}
        {"" if f.doc_type else RESERVED_SLOTS}
        order by score desc
        limit %(k)s
    """
    return sql, params


# Owner complaints are written in the first person, like users' questions, so they out-score
# recall notices on similarity even when a recall is the better answer. Without a doc_type
# filter, keep at most 3 complaints so the 2 best recalls always make the top 5.
RESERVED_SLOTS = "qualify row_number() over (partition by doc_type order by score desc) <= iff(doc_type = 'recall', 2, 3)"


def embed_question(conn, question: str) -> str:
    """One Cortex call: the question's embedding as JSON text, ready to bind into search_sql."""
    cur = conn.cursor()
    cur.execute(
        f"select to_json(snowflake.cortex.embed_text_1024('{EMBEDDING_MODEL}', %(q)s)::array)",
        {"q": QUERY_PREFIX + question},
    )
    return cur.fetchone()[0]


@dataclass
class Doc:
    doc_id: str
    doc_type: str
    source_id: str
    doc_date: str
    makes: list
    model_families: list
    model_years: list
    component_groups: list
    is_severe: bool
    text: str
    score: float

    @property
    def url(self) -> str:
        if self.doc_type == "recall":
            return f"https://api.nhtsa.gov/recalls/campaignNumber?campaignNumber={self.source_id}"
        return f"https://api.nhtsa.gov/complaints/odinumber?odinumber={self.source_id}"

    def to_dict(self) -> dict:
        return asdict(self) | {"url": self.url}


def search(conn, question: str, f: Filters, k: int = 5) -> list[Doc]:
    sql, params = search_sql(f)
    cur = conn.cursor()
    cur.execute(sql, params | {"question_vector": embed_question(conn, question), "k": k})
    docs = []
    for row in cur.fetchall():
        row = list(row)
        for i in (4, 5, 6, 7):  # ARRAY columns arrive as JSON text
            row[i] = json.loads(row[i]) if isinstance(row[i], str) else row[i]
        row[3] = str(row[3])
        docs.append(Doc(*row))
    return docs


def load_vocabulary(conn) -> tuple[dict[str, set[str]], list[str]]:
    """Makes with their model families, and the component groups, as they appear in the documents."""
    cur = conn.cursor()
    cur.execute(f"""
        select distinct m.value::string, f.value::string
        from {TABLE}, lateral flatten(makes) m, lateral flatten(model_families) f
        where array_size(makes) = 1
    """)
    vocab: dict[str, set[str]] = {}
    for make, family in cur.fetchall():
        vocab.setdefault(make, set()).add(family)
    cur.execute(f"select distinct g.value::string from {TABLE}, lateral flatten(component_groups) g order by 1")
    groups = [r[0] for r in cur.fetchall()]
    return vocab, groups
