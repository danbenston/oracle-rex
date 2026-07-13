"""Deterministic retrieval over the Living Rules Reference FTS5 index.

No LLM, no API key, no per-request cost — just BM25 over the vendored corpus.
``retrieve(question, k)`` is the one public entry point; it tokenizes the
question, expands TI jargon via :mod:`.aliases`, and runs a weighted BM25 query
against the standalone index built by ``manage.py build_rules_index``.

Kept separate from the app DB on purpose (read-only reference data): the index
lives in its own sqlite file so it never collides with the SQLite/Postgres app
database and can be rebuilt cheaply at deploy time.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .aliases import expand_terms
from .builder import build_index

_CORE_DIR = Path(__file__).resolve().parents[2]  # rules_index -> service -> core
SOURCE_PATH = _CORE_DIR / "data" / "source" / "lrr" / "lrr_rules.json"
INDEX_PATH = _CORE_DIR / "data" / "index" / "lrr_fts.sqlite3"

DEFAULT_K = 8

# BM25 column weights (topic, text). A topic-name match is a strong signal for
# short questions ("nebula?", "what is capacity?"), so topic is weighted well
# above body text.
_TOPIC_WEIGHT = 10.0
_TEXT_WEIGHT = 1.0

# Query-only stopwords: question scaffolding that carries no retrieval signal.
# Kept short on purpose — BM25's IDF already down-weights common words; this just
# trims obvious noise so a bare "can i ..." doesn't match on "can"/"i".
_STOPWORDS = frozenset(
    """
    a an the this that these those i you my your we they it he she
    can could do does did is are was were be been being am
    to of in on at by for with from into as if and or but not no
    what when where which who whom how why whether
    have has had will would shall should may might must
    me us them him her its our their any some all each both
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class RulesIndexError(RuntimeError):
    """Raised when the FTS5 index is missing or unreadable."""


@dataclass(frozen=True)
class RetrievedRule:
    rule_id: str
    topic: str
    text: str
    kind: str
    score: float  # higher == better (negated BM25)


def _tokenize(question: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(question.lower()) if t not in _STOPWORDS]


def _build_match(question: str) -> str | None:
    """Turn a natural-language question into a safe FTS5 MATCH expression.

    Tokens are alias-expanded and OR'd; each is wrapped as an FTS5 string literal
    so punctuation/operators in a term can never be interpreted as query syntax.
    Returns None when the question has no usable terms.
    """
    terms = expand_terms(_tokenize(question))
    if not terms:
        return None
    # Escape embedded double quotes (defensive; tokens are [a-z0-9]+ so this is
    # essentially a no-op) and wrap each term as an FTS5 string.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _connect_ro(index_path: Path) -> sqlite3.Connection:
    if not index_path.exists():
        raise RulesIndexError(
            f"Rules index not found at {index_path}. Build it with "
            "'python manage.py build_rules_index'."
        )
    return sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)


def retrieve(
    question: str, k: int = DEFAULT_K, index_path: str | Path | None = None
) -> list[RetrievedRule]:
    """Return up to *k* rule chunks most relevant to *question*, best first.

    Deterministic and side-effect-free. ``index_path`` overrides the default
    location (used by tests to point at a freshly built temp index).
    """
    match = _build_match(question)
    if match is None:
        return []

    path = Path(index_path) if index_path is not None else INDEX_PATH
    conn = _connect_ro(path)
    try:
        rows = conn.execute(
            "SELECT rule_id, topic, text, kind, "
            f"       bm25(rules, {_TOPIC_WEIGHT}, {_TEXT_WEIGHT}) AS score "
            "FROM rules WHERE rules MATCH ? ORDER BY score LIMIT ?",
            (match, k),
        ).fetchall()
    except sqlite3.OperationalError as exc:  # malformed MATCH, corrupt index, ...
        raise RulesIndexError(f"Rules index query failed: {exc}") from exc
    finally:
        conn.close()

    # bm25() returns more-negative for better matches; negate so higher == better.
    return [
        RetrievedRule(rule_id=r[0], topic=r[1], text=r[2], kind=r[3], score=-r[4])
        for r in rows
    ]


__all__ = [
    "retrieve",
    "RetrievedRule",
    "RulesIndexError",
    "build_index",
    "SOURCE_PATH",
    "INDEX_PATH",
    "DEFAULT_K",
]
