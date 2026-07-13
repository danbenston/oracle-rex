"""Build the standalone SQLite FTS5 index for the Living Rules Reference corpus.

The corpus (``core/data/source/lrr/lrr_rules.json``, produced by
``scripts/ingest_lrr.py``) is small (~600 chunks), so FTS5 with BM25 ranking —
which ships in Python's stdlib ``sqlite3`` — is the whole retrieval engine: zero
new dependencies, zero per-request cost, deterministic. The index is written to
its OWN sqlite file (``core/data/index/lrr_fts.sqlite3``), separate from the app
DB: it is read-only reference data, not app state, which sidesteps the
SQLite-vs-Postgres split and lets a deploy rebuild it cheaply (like
``collectstatic``).

``topic`` and ``text`` are indexed; ``rule_id`` / ``parent_topic`` / ``kind`` are
stored but UNINDEXED (returned with each hit). Topic-name matches are a strong
signal for short questions, so retrieval weights ``topic`` above ``text`` at
query time via ``bm25()``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# Porter stemmer + unicode61 folding so "retreat" matches "retreating" and case/
# punctuation don't matter. Column order matters: indexed columns first, so the
# bm25() weights (topic, text) line up positionally.
_CREATE_SQL = """
CREATE VIRTUAL TABLE rules USING fts5(
    topic,
    text,
    rule_id UNINDEXED,
    parent_topic UNINDEXED,
    kind UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


def build_index(source_json_path: str | Path, index_path: str | Path) -> dict:
    """Build the FTS5 index at *index_path* from the corpus JSON.

    Overwrites any existing index file (a rebuild is cheap and deterministic).
    Returns a small stats dict for the caller to report.
    """
    source_json_path = Path(source_json_path)
    index_path = Path(index_path)

    data = json.loads(source_json_path.read_text(encoding="utf-8"))
    chunks = data.get("chunks", [])
    version = (data.get("provenance") or {}).get("version", "")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index_path.unlink()

    conn = sqlite3.connect(str(index_path))
    try:
        conn.execute(_CREATE_SQL)
        # A tiny meta table records provenance so a stale index is detectable.
        conn.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);")
        conn.executemany(
            "INSERT INTO rules (topic, text, rule_id, parent_topic, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    c.get("topic", ""),
                    c.get("text", ""),
                    c.get("rule_id", ""),
                    c.get("parent_topic") or "",
                    c.get("kind", ""),
                )
                for c in chunks
            ],
        )
        conn.executemany(
            "INSERT INTO index_meta (key, value) VALUES (?, ?)",
            [
                ("source_version", str(version)),
                ("chunk_count", str(len(chunks))),
                ("source", str(source_json_path.name)),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    topics = sum(1 for c in chunks if c.get("kind") == "topic_intro")
    rules = sum(1 for c in chunks if c.get("kind") == "rule")
    return {
        "index_path": str(index_path),
        "chunks": len(chunks),
        "topics": topics,
        "rules": rules,
        "source_version": version,
    }
