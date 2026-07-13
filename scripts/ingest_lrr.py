#!/usr/bin/env python3
"""Ingest the Twilight Imperium Living Rules Reference (LRR) PDF into structured
rule chunks for the Rules Q&A retrieval corpus.

Corpus source (Phase 0 of .features/rules_rag_grounding.md): the **official**
FFG/Asmodee *Prophecy of Kings Living Rules Reference 2.0* PDF (base + PoK, with
errata folded into that revision). The PDF is Asmodee/FFG IP and is **not
committed** — this script parses a locally supplied copy into the vendored,
committed ``core/data/source/lrr/lrr_rules.json`` (mechanical rules text only,
attributed via the provenance header). Re-run it by hand when the LRR revs.

Why this parses cleanly (see the design notes below): the LRR is an alphabetized
Rules Reference. Each topic is an ALL-CAPS heading with a topic number; each
numbered sub-rule (``18.2``) is the natural citation granularity players already
use on forums. So we **chunk by rule number, not by token window**:

  * one chunk per topic intro   -> rule_id "18"     (kind="topic_intro")
  * one chunk per numbered rule  -> rule_id "18.2"  (kind="rule")

Lettered sub-clauses (a, b, ...) are part of their sub-rule and stay embedded in
that rule's text (prefixed "(a) ") rather than becoming separate chunks.

Two PDF-layout facts drive the parser, both verified against LRR 2.0:

  1. **Two columns.** Naive text extraction interleaves them; we split words by
     x-midpoint (page center) and read left column fully, then right.
  2. **A tiny-font quick-reference diagram** on some pages extracts as scrambled
     letter-spaced garbage. It is rendered at ~1.2pt while real body text is
     9.5pt, so a ``size >= MIN_FONT_SIZE`` filter removes it entirely.

Marker detection is resolution-independent: topic numbers, ``N.M`` sub-rule
numbers, and clause letters all hang in a LEFT gutter, so a line's trailing
marker token always has a smaller x0 than every other word on that line.

Usage:
    python scripts/ingest_lrr.py --pdf path/to/lrr.pdf \
        --out core/data/source/lrr/lrr_rules.json --version 2.0

Requires pdfplumber (dev-only; not in requirements.txt). Install with:
    pip install pdfplumber
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import unicodedata
from pathlib import Path

MIN_FONT_SIZE = 5.0  # body text is 9.5pt; the diagram noise is ~1.2pt
LINE_Y_TOLERANCE = 4.0  # words within this many points of top are one visual line

# A line's trailing token is a gutter marker if it matches one of these AND sits
# left of every other word on the line (the hanging left gutter).
_RE_RULE = re.compile(r"^\d+\.\d+$")       # sub-rule number, e.g. 18.2
_RE_TOPIC_NUM = re.compile(r"^\d+$")        # topic number, e.g. 18
_RE_LETTER = re.compile(r"^[a-z]$")         # clause letter, e.g. a

# A topic heading with the number left INLINE (survives when a trailing
# "(QUALIFIER)" token, not the number, is the line's last token). e.g.
# "COMBAT 18 (ATTRIBUTE)".
_RE_HEADING_INLINE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9 \-'&/]+?)\s+(?P<num>\d+)(?:\s+\((?P<qual>[A-Z /-]+)\))?$"
)
# The heading NAME alone (used when the topic number was stripped off as the
# hanging left-gutter marker). e.g. "COMMAND SHEET", "ANTI-FIGHTER BARRAGE".
_RE_HEADING_NAME = re.compile(r"^(?P<name>[A-Z][A-Z0-9 \-'&/]+?)(?:\s+\((?P<qual>[A-Z /-]+)\))?$")
_RE_RELATED = re.compile(r"^RELATED TOPICS:\s*(?P<body>.*)$")


def _detect_heading(marker, stripped):
    """Return (num, name, qualifier) if this line is a topic heading, else None.

    Two shapes: the number left inline (marker is None, e.g. a "(QUALIFIER)"
    trailed the line), or the number stripped off as the gutter marker (marker is
    a bare integer and the text is just the ALL-CAPS name).
    """
    if marker and _RE_TOPIC_NUM.match(marker) and stripped:
        m = _RE_HEADING_NAME.match(stripped)
        if m and len(m.group("name").split()) <= 6:
            return marker, m.group("name").strip(), m.group("qual")
        return None
    if not marker and stripped:
        m = _RE_HEADING_INLINE.match(stripped)
        if m and len(m.group("name").split()) <= 6:
            return m.group("num"), m.group("name").strip(), m.group("qual")
    return None


def _normalize(text: str) -> str:
    """Collapse whitespace and normalize unicode (curly quotes are preserved)."""
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def _column_rows(page, split: float):
    """Return [(left_rows, right_rows)] where each rows entry is a list of words
    (dicts with text/x0/x1/top) grouped into one visual line, in reading order.
    """
    filtered = page.filter(lambda o: (o.get("size") or 99) >= MIN_FONT_SIZE)
    words = filtered.extract_words(use_text_flow=False, keep_blank_chars=False)

    def rows_for(col_words):
        col_words = sorted(col_words, key=lambda w: (round(w["top"]), w["x0"]))
        rows, cur, cy = [], [], None
        for w in col_words:
            if cy is None or abs(w["top"] - cy) > LINE_Y_TOLERANCE:
                if cur:
                    rows.append(cur)
                cur, cy = [w], w["top"]
            else:
                cur.append(w)
        if cur:
            rows.append(cur)
        return rows

    left = rows_for([w for w in words if w["x0"] < split])
    right = rows_for([w for w in words if w["x0"] >= split])
    return left, right


def _trailing_marker(row):
    """If the row's last token is a hanging left-gutter marker, return
    (marker_text, remaining_words); else (None, row). A marker sits left of every
    other word on the line.
    """
    if len(row) < 2:
        return None, row
    last = row[-1]
    text = last["text"]
    if not (_RE_RULE.match(text) or _RE_TOPIC_NUM.match(text) or _RE_LETTER.match(text)):
        return None, row
    others_min_x0 = min(w["x0"] for w in row[:-1])
    if last["x0"] < others_min_x0:
        return text, row[:-1]
    return None, row


def _row_text(words) -> str:
    return " ".join(w["text"] for w in words)


def _iter_page_lines(pdf):
    """Yield (marker, text, is_heading_candidate) for every content line in
    reading order across the whole document. ``marker`` is the hanging gutter
    token (rule/topic number or clause letter) or None.
    """
    for page in pdf.pages:
        split = page.width / 2
        for rows in _column_rows(page, split):
            for row in rows:
                marker, body = _trailing_marker(row)
                text = _row_text(body)
                yield marker, text


class Topic:
    def __init__(self, num, name, qualifier):
        self.num = num
        self.name = name if not qualifier else f"{name} ({qualifier})"
        self.intro_parts: list[str] = []
        self.related: list[str] = []
        self.rules: dict[str, list[str]] = {}  # rule_id -> text parts
        self.rule_order: list[str] = []


def _title_case_topic(name: str, qualifier: str | None) -> str:
    base = " ".join(w if w.isupper() and len(w) <= 3 else w.capitalize()
                     for w in name.split())
    if qualifier:
        q = qualifier.strip().capitalize()
        return f"{base} ({q})"
    return base


def parse(pdf) -> list[dict]:
    topics: list[Topic] = []
    current: Topic | None = None
    current_rule: str | None = None      # "18.2" while collecting a sub-rule
    collecting_related = False
    related_buf = ""

    for marker, text in _iter_page_lines(pdf):
        stripped = text.strip()

        # Bare page-number / stray-integer lines (no real text) — skip so they
        # never pollute a rule's text.
        if not stripped:
            continue
        if stripped.isdigit() and marker is None:
            continue

        # A "RELATED TOPICS:" list wraps across lines and the wrap can fall
        # mid-name (".. Anti-Fighter" / "Barrage, .."), so accumulate the whole
        # block into one string and comma-split it only when the block ends.
        if collecting_related:
            if _is_related_continuation(marker, stripped) and not _detect_heading(marker, stripped):
                related_buf += " " + stripped
                continue
            current.related = _dedupe(_split_related(related_buf))
            collecting_related = False
            related_buf = ""
            # fall through: process this line (heading / rule / prose) normally

        related_match = _RE_RELATED.match(stripped)
        if related_match and current:
            related_buf = related_match.group("body")
            collecting_related = True
            current_rule = None
            continue

        heading = _detect_heading(marker, stripped)
        if heading:
            num, name, qual = heading
            current = Topic(num, _title_case_topic(name, qual), None)
            topics.append(current)
            current_rule = None
            continue

        if current is None:
            continue  # front matter before the first topic (title page, etc.)

        # Standalone marker: a sub-rule number (or clause letter) printed ALONE
        # on its line, its body on the following line(s). This happens right
        # after an ALL-CAPS numbered sub-header (e.g. "PUBLIC OBJECTIVES" then
        # "61.11" then the body). Promote it to a marker with empty inline text
        # so the block below starts the rule and later lines fill its body.
        if marker is None and _RE_RULE.match(stripped):
            marker, stripped = stripped, ""
        elif marker is None and _RE_LETTER.match(stripped) and current_rule:
            marker, stripped = stripped, ""

        # A sub-rule marker starts a new rule for the current topic.
        if marker and _RE_RULE.match(marker):
            rule_id = marker
            current.rules.setdefault(rule_id, [])
            current.rule_order.append(rule_id)
            current_rule = rule_id
            if stripped:
                current.rules[rule_id].append(stripped)
            continue

        # A clause letter: keep it embedded in the current sub-rule, prefixed.
        if marker and _RE_LETTER.match(marker) and current_rule:
            seg = f"({marker}) {stripped}".strip()
            current.rules[current_rule].append(seg)
            continue

        # Drop pure all-caps sub-section labels (COSTS, TIMING, ...): navigation,
        # not rule text. (RELATED TOPICS handled above.)
        if stripped and stripped == stripped.upper() and len(stripped) > 2 \
                and any(c.isalpha() for c in stripped) and "," not in stripped:
            continue

        # Otherwise: body text -> current rule, or the topic intro.
        if not stripped:
            continue
        if current_rule:
            current.rules[current_rule].append(stripped)
        else:
            current.intro_parts.append(stripped)

    if collecting_related and current:  # related list ran to the end of the doc
        current.related = _dedupe(_split_related(related_buf))

    return _to_chunks(topics)


def _is_related_continuation(marker, stripped: str) -> bool:
    """A wrapped RELATED TOPICS continuation line: a short comma-separated list
    of Title-Case topic names (every word capitalized), never a prose sentence.
    """
    if marker is not None or not stripped:
        return False
    words = [w for w in stripped.replace(",", " ").split() if w]
    if not words or len(words) > 8:
        return False
    return all(w[0].isupper() or not w[0].isalpha() for w in words)


def _split_related(body: str) -> list[str]:
    # Drop the appendix section names ("INDEX"/"GLOSSARY") that can bleed into
    # the last topic's wrapped related list at the end of the document — both as
    # a standalone item and as a trailing word on the final item (the wrap joins
    # "Wormhole Nexus" + "INDEX" with no comma between them).
    out = []
    for t in body.split(","):
        t = t.strip().rstrip(".")
        for appendix in ("INDEX", "GLOSSARY"):
            if t.upper().endswith(" " + appendix):
                t = t[: -(len(appendix) + 1)].strip()
        if t and t.upper() not in ("INDEX", "GLOSSARY"):
            out.append(t)
    return out


def _to_chunks(topics: list[Topic]) -> list[dict]:
    chunks = []
    for t in topics:
        intro = _normalize(" ".join(t.intro_parts))
        chunks.append({
            "rule_id": t.num,
            "topic": t.name,
            "parent_topic": None,
            "kind": "topic_intro",
            "text": intro,
            "related": _dedupe(t.related),
        })
        for rule_id in t.rule_order:
            body = _normalize(" ".join(t.rules[rule_id]))
            if not body:
                # A number printed alone whose body was claimed by an inline
                # marker on the next line (the PDF's own layout is faithful — the
                # inline number labels that paragraph). Nothing to emit.
                continue
            chunks.append({
                "rule_id": rule_id,
                "topic": t.name,
                "parent_topic": t.num,
                "kind": "rule",
                "text": body,
                "related": [],
            })
    return chunks


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def build(pdf_path: Path, version: str, source_url: str) -> dict:
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        chunks = parse(pdf)
    for c in chunks:
        c["source_version"] = version
    return {
        "provenance": {
            "source": "Twilight Imperium: Fourth Edition — Prophecy of Kings "
                      "Living Rules Reference",
            "version": version,
            "source_url": source_url,
            "publisher": "Fantasy Flight Games / Asmodee",
            "codex_coverage": "Errata folded into the LRR as published at this "
                              "revision; later standalone Codex documents are not "
                              "separately verified (Phase 0 open item).",
            "ip_note": "Mechanical rules text vendored for a free fan tool "
                       "(RAG grounding / citations). Rules text is Asmodee/FFG IP "
                       "and is attributed wherever cited. The source PDF is NOT "
                       "committed; this JSON is produced by scripts/ingest_lrr.py.",
            "ingested_by": "scripts/ingest_lrr.py",
            "ingested_at": datetime.date.today().isoformat(),
        },
        "chunks": chunks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, type=Path, help="Path to the LRR PDF.")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output JSON path (e.g. core/data/source/lrr/lrr_rules.json).")
    ap.add_argument("--version", default="2.0", help="LRR version to stamp.")
    ap.add_argument("--source-url", default=(
        "https://images-cdn.fantasyflightgames.com/filer_public/51/55/"
        "51552c7f-c05c-445b-84bf-4b073456d008/ti10_pok_living_rules_reference_20_web.pdf"
    ), help="Provenance URL of the source PDF.")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    data = build(args.pdf, args.version, args.source_url)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    chunks = data["chunks"]
    topics = [c for c in chunks if c["kind"] == "topic_intro"]
    rules = [c for c in chunks if c["kind"] == "rule"]
    print(f"Wrote {args.out}")
    print(f"  topics: {len(topics)}   sub-rules: {len(rules)}   total chunks: {len(chunks)}")
    if topics:
        nums = [int(t["rule_id"]) for t in topics]
        print(f"  topic numbers: {min(nums)}..{max(nums)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
