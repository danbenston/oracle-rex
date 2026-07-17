#!/usr/bin/env python3
"""Ingest Twilight Imperium technology cards into a structured corpus.

Phase 1 of .features/technology_corpus.md. The corpus serves three consumers:

  * **Rules Q&A (now)** — card text becomes retrievable, so "what does Gravity
    Drive do?" is answered from real card text with a citation. The LRR contains
    the rules *about* technology but no card text at all.
  * **Tactical Calculator (later)** — ``unit_upgrade`` stats replace the
    base-stats-only simulation in ``core/service/combat/units.py``.
  * **Strategy (later)** — colors + prerequisites enable tech-path reasoning.

Source: the TI4 Fandom wiki, via the MediaWiki API (``action=parse&prop=wikitext``).
The LRR pattern (parse an official PDF) does not transfer — FFG publishes no
document containing tech card text. We parse **wikitext, not rendered HTML**: it
is structured, stable, and free of presentation noise. (The rendered pages return
HTTP 402 to non-browser clients; the API returns 200.)

This is a **by-hand, occasional** script, exactly like ``scripts/ingest_lrr.py``.
Nothing in the request path may ever call the wiki: it writes a vendored,
committed JSON corpus that the app reads offline.

Usage:
    python scripts/ingest_tech.py --out core/data/source/tech/ti_technologies.json
    python scripts/ingest_tech.py --cache-dir /tmp/wiki --out ...   # reuse fetches

Stdlib only.

---

Design notes (each earned the hard way in the Phase 0 survey; see the epic doc):

**There is no single wiki format.** Five page families, five parsers. Do not try
to generalize them.

**Prerequisites vs. the card's own colour badge.** Both are ``{{Tech|colour}}``
template calls, often in the same table. A *bare* call is a prerequisite; one
carrying a ``w=`` argument (``{{Tech|biotic|w=32px}}``) is the card's own colour
badge. Counting naively gives every tech one phantom prerequisite. Verified
against Neural Motivator (0), Dacxive Animators (1), Hyper Metabolism (2).

**Editions.** ``{{Edition|X}}`` is sporadic — absent from unit upgrades and all
DS pages, and present on only about half of colour-page cards. So the section
heading sets the default and a per-card ``{{Edition|X}}`` overrides it.

**Thunder's Edge is excluded** (see the epic doc). It is filtered three ways
because it leaks in three ways: by section heading, by per-card Edition template,
and — on the unit-upgrade page, which has no edition markers whatsoever — by the
faction the upgrade belongs to.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://twilight-imperium.fandom.com/api.php"
USER_AGENT = "oracle-rex-tech-ingest/0.1 (Twilight Imperium fan tool)"
REQUEST_TIMEOUT = 30

# Editions we do NOT ingest. Thunder's Edge is official and current, but the app
# models no TE factions and the LRR corpus is PoK-era, so TE tech would be
# grounded against rules that do not cover it.
EXCLUDED_EDITIONS = frozenset({"Thunder's Edge"})

# Section headings that are not technology content.
SKIP_SECTIONS = frozenset({"FAQ", "Gallery", "See Also", "References"})

TECH_COLORS = frozenset({"biotic", "cybernetic", "propulsion", "warfare"})


class IngestError(RuntimeError):
    """Raised when a page's structure does not match what the parser expects."""


# --- wikitext helpers ------------------------------------------------------

_RE_HEADING = re.compile(r"^(={2,4})\s*(.+?)\s*\1\s*$", re.M)
# Any {{Tech|colour}} call, with or without further arguments (`|w=32px`).
#
# NB: do NOT try to tell prerequisites from the card's own colour badge by
# whether the call carries a `w=` argument. That rule looks right on most cards
# and is wrong: Self Assembly Routines writes its prerequisite as
# `{{Tech|warfare|w=32px}}`, identical to a badge, and the rule silently drops a
# real prerequisite. Each family's parser separates them by POSITION instead —
# see _color_prereqs (badge is the trailing `text-align: right` cell) and
# _faction_techs_in (badge leads; prerequisites live in the blockquote).
_RE_PREREQ = re.compile(r"\{\{Tech\|(\w+)(?:\|[^}]*)?\}\}", re.I)
_RE_EDITION = re.compile(r"\{\{Edition\|([^}|]+)\}\}", re.I)
_RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_RE_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


def strip_wikilinks(text: str) -> str:
    """``[[A|B]]`` -> ``B``; ``[[A]]`` -> ``A``."""
    return _RE_WIKILINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)


def clean_text(text: str) -> str:
    """Reduce wikitext to the plain card text a reader (or a model) would see."""
    text = strip_wikilinks(text)
    text = _RE_TEMPLATE.sub("", text)          # {{Tech|..}}, {{Edition|..}}, ...
    text = _RE_HTML_TAG.sub(" ", text)         # <blockquote>, <big>, <br />, <u>
    text = text.replace("'''", "").replace("''", "")
    text = text.replace("&nbsp;", " ").replace("’", "'").replace("–", "-")
    text = _RE_WS.sub(" ", text)
    return text.strip(" *: ")


def parse_prereqs(fragment: str) -> list[str]:
    """Every tech colour referenced in *fragment*.

    Callers must hand in a fragment that contains ONLY prerequisites — the card's
    own colour badge is indistinguishable at this level (see ``_RE_PREREQ``).
    """
    return [c.lower() for c in _RE_PREREQ.findall(fragment) if c.lower() in TECH_COLORS]


# The card's colour badge sits in the table's trailing right-aligned cell.
_RE_BADGE_CELL = re.compile(r"text-align:\s*right", re.I)


def _color_prereqs(table: str) -> list[str]:
    """Prerequisites from a colour-page table, by position.

    The badge is the LAST right-aligned cell; everything before it that names a
    tech colour is a prerequisite. Positional because the markup itself is
    ambiguous: a prerequisite cell may be written exactly like a badge.
    """
    matches = list(_RE_BADGE_CELL.finditer(table))
    if matches:
        return parse_prereqs(table[: matches[-1].start()])
    # No right-aligned badge cell: fall back to dropping the final call, which is
    # the badge on every table shape seen so far.
    calls = list(_RE_PREREQ.finditer(table))
    if not calls:
        return []
    return parse_prereqs(table[: calls[-1].start()])


def find_edition(fragment: str) -> str | None:
    m = _RE_EDITION.search(fragment)
    return m.group(1).strip() if m else None


def heading_name(raw: str) -> str:
    """A heading's display text, with links/templates/markup removed."""
    return clean_text(raw)


def is_faction_heading(raw: str) -> bool:
    """True when a heading is a faction wikilink rather than a tech name.

    Colour pages mix both: ``=== Hyper Metabolism ===`` is a tech, while
    ``=== [[The Arborec]] ===`` opens a faction subsection whose techs we take
    from the canonical Faction Technologies page instead.
    """
    return raw.strip().startswith("[[")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s


# Codex "Omega" revisions of a card share its name but are distinct cards (their
# own prerequisites and text). The wiki marks them two ways, and a corpus must
# keep them apart: colour/unit pages put Ω in the NAME ("Magen Defense Grid Ω"),
# while the Faction Technologies page puts it in the TEXT ("Ω: After you ..."). A
# slug that ignored Ω would collapse all revisions onto one id.
_OMEGA = "Ω"
_RE_OMEGA_IN_TEXT = re.compile(rf"^\s*({_OMEGA}+)\s*:")


def omega_level(name: str, text: str = "") -> int:
    """0 for a base card, 1 for Ω (Codex), 2 for ΩΩ — from the name or text marker."""
    n = name.count(_OMEGA)
    if not n:
        m = _RE_OMEGA_IN_TEXT.match(text)
        if m:
            n = len(m.group(1))
    return min(n, 2)


def make_id(name: str, text: str = "") -> str:
    """Citation id for a tech, keeping Omega revisions distinct."""
    return slugify(name.replace(_OMEGA, "")) + "_omega" * omega_level(name, text)


def make_name(name: str, text: str = "") -> str:
    """Display name with a trailing Ω/ΩΩ when the revision is marked only in text."""
    base = name.replace(_OMEGA, "").strip()
    lvl = omega_level(name, text)
    return f"{base} {_OMEGA * lvl}" if lvl else base


def iter_sections(wikitext: str, level: int):
    """Yield ``(title_raw, body)`` for each heading at *level*.

    Body runs to the next heading of the same or shallower level, so a section's
    subsections stay inside its body.
    """
    marks = []
    for m in _RE_HEADING.finditer(wikitext):
        marks.append((len(m.group(1)), m.group(2), m.start(), m.end()))
    for i, (lvl, title, _start, end) in enumerate(marks):
        if lvl != level:
            continue
        stop = len(wikitext)
        for nlvl, _nt, nstart, _ne in marks[i + 1:]:
            if nlvl <= lvl:
                stop = nstart
                break
        yield title, wikitext[end:stop]


# --- fetching --------------------------------------------------------------

def fetch_wikitext(page: str, cache_dir: Path | None = None) -> str:
    """Fetch a page's wikitext, optionally caching to disk between runs."""
    cache_file = None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / (slugify(page) + ".wikitext")
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

    url = f"{API}?action=parse&page={urllib.parse.quote(page)}&prop=wikitext&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.load(resp)
    if "error" in data:
        raise IngestError(f"wiki API error for {page!r}: {data['error'].get('info')}")
    text = data["parse"]["wikitext"]["*"]
    if cache_file:
        cache_file.write_text(text, encoding="utf-8")
    return text


# --- edition / faction mapping ---------------------------------------------

# Wiki edition label -> the `set` vocabulary the app already uses in
# core/data/source/milty_draft_factions.json (base / pok / keleres / discordant /
# discordantexp). Reusing it keeps `faction` cross-referenceable against the
# app's own faction ids instead of inventing a parallel vocabulary.
EDITION_TO_SET = {
    "Base Game": "base",
    "Prophecy of Kings": "pok",
    "Codex I": "codex_i",
    "Codex II": "codex_ii",
    "Codex III": "keleres",   # Codex III is the Council Keleres release
    "Codex IV": "codex_iv",
}


def edition_to_set(edition: str) -> str:
    """Normalize a wiki edition label to the app's `set` vocabulary."""
    if edition in EXCLUDED_EDITIONS:
        raise IngestError(f"edition {edition!r} is excluded and must be filtered earlier")
    try:
        return EDITION_TO_SET[edition]
    except KeyError:
        raise IngestError(
            f"unknown edition {edition!r}. A new expansion probably shipped: decide "
            "whether it is in scope, then add it to EDITION_TO_SET or EXCLUDED_EDITIONS."
        )


# Codex sections are phrased freely across pages: "Codex III", but also
# "[[Codex#Volume III (Vigil)|The Codex Volume III: Vigil]]". Match the roman
# numeral near the word Codex rather than any one spelling.
_RE_CODEX = re.compile(r"Codex\W+(?:Volume\s+)?(IV|III|II|I)\b", re.I)


def section_edition(title_raw: str) -> str | None:
    """The edition a section heading denotes, or None if it isn't an edition heading.

    Handles colour-page phrasing ("Technology introduced in [[Prophecy of
    Kings]]", "Basic Technology"), the Faction Technologies page's
    ("[[Base Game (Fourth Edition)|Base Game]] {{Edition|Base Game}}"), and the
    several ways Codex volumes are written.
    """
    tmpl = find_edition(title_raw)
    if tmpl:
        return tmpl
    name = heading_name(title_raw)
    if name in ("Basic Technology", "Base Game"):
        return "Base Game"
    # Check the raw title too: it keeps the link target ("Codex#Volume III ..."),
    # which sometimes carries the numeral when the display text does not.
    cm = _RE_CODEX.search(name) or _RE_CODEX.search(title_raw)
    if cm:
        return f"Codex {cm.group(1).upper()}"
    for label in list(EDITION_TO_SET) + sorted(EXCLUDED_EDITIONS):
        if label.lower() in name.lower():
            return label
    return None


# --- family 1: colour pages (Biotic / Cybernetic / Propulsion / Warfare) ----

def parse_color_page(wikitext: str, color: str) -> list[dict]:
    """Non-faction techs from a colour page.

    Structure: ``== <edition section> ==`` -> ``=== <Tech Name> ===`` -> an
    article-table holding prerequisites and the card text as a bullet list.

    Faction subsections (``=== [[The Arborec]] ===``, whose techs hang off ``h4``)
    are SKIPPED here: the same techs appear on the canonical Faction Technologies
    page, and taking both would duplicate every faction tech.
    """
    out = []
    for sect_title, body in iter_sections(wikitext, 2):
        name = heading_name(sect_title)
        if name in SKIP_SECTIONS:
            continue
        edition = section_edition(sect_title)
        if edition in EXCLUDED_EDITIONS:
            continue
        if edition is None:
            # "Faction Technology" is the expected unlabelled section; anything
            # else unlabelled means the page grew a shape we haven't seen.
            if "faction" in name.lower():
                continue
            raise IngestError(
                f"{color} page: section {name!r} has no recognizable edition. "
                "Survey the page before assuming a default."
            )

        for tech_title, tech_body in iter_sections(body, 3):
            if is_faction_heading(tech_title):
                continue  # faction techs come from Faction Technologies
            base_name = heading_name(tech_title)
            out.extend(_color_techs_in_section(tech_body, base_name, color, edition))
    return out


# A wikitable. Not nested on these pages, so a non-greedy span is safe.
_RE_TABLE = re.compile(r"\{\|.*?\n\|\}", re.S)
# The table's header cell: everything between the `!`-row and the first `|-`.
# It holds the card's exact name and (usually) its {{Edition|..}}.
_RE_TABLE_HEADER = re.compile(r"^!\s*[^|\n]*\|\s*\n(?P<h>.*?)(?=^\|-)", re.S | re.M)
_RE_BULLET = re.compile(r"^\*+\s*(.+?)\s*$", re.M)


def _color_techs_in_section(body: str, base_name: str, color: str, default_edition: str) -> list[dict]:
    """One record per TABLE, not per section.

    A single heading can hold several versions of the same card: X-89 Bacterial
    Weapon ships as base, Ω (Codex I) and ΩΩ (Codex IV), each in its own table
    with its own edition. Treating the section as one card silently summed all
    three cards' prerequisites (3+3+3=9) and glued their texts together.
    """
    out = []
    for table in _RE_TABLE.findall(body):
        hm = _RE_TABLE_HEADER.search(table)
        header = hm.group("h") if hm else ""
        edition = find_edition(header) or find_edition(table) or default_edition
        if edition in EXCLUDED_EDITIONS:
            continue
        raw_name = clean_text(header) or base_name
        text = _bullet_text(table)
        if not text:
            raise IngestError(f"no card text found for {raw_name!r} on the {color} page")
        name = make_name(raw_name, text)
        out.append({
            "id": make_id(raw_name, text),
            "name": name,
            "base_name": base_name,
            "color": color,
            "prerequisites": _color_prereqs(table),
            "set": edition_to_set(edition),
            "faction": None,
            "exhaustible": False,
            "text": text,
            "starting_for": [],
            "unit_upgrade": None,
        })
    if not out:
        raise IngestError(f"no card tables found under {base_name!r} on the {color} page")
    return out


def _bullet_text(body: str) -> str:
    """Card text from a colour-page table, which renders it as a bullet list."""
    bullets = [clean_text(b) for b in _RE_BULLET.findall(body)]
    return " ".join(b for b in bullets if b)


# --- family 2: Faction Technologies (canonical for faction techs) -----------

# `{{Tech|colour}} '''Name:''' card text<blockquote>Prerequisites: {{Tech|c}}</blockquote>`
# The LEADING {{Tech|colour}} is the card's own colour, not a prerequisite — only
# the blockquote's calls are prerequisites. This is the inverse of the colour
# pages, where the badge is trailing and carries `w=`.
_RE_FACTION_TECH = re.compile(
    r"\{\{Tech\|(?P<color>\w+)\}\}\s*'''\s*(?P<name>[^:']+?)\s*:?\s*'''\s*:?"
    r"(?P<rest>.*?)(?=\{\{Tech\|\w+\}\}\s*'''|\Z)",
    re.S,
)
_RE_PREREQ_BLOCK = re.compile(r"Prerequisites?\s*:(?P<p>.*?)(?:</blockquote>|\Z)", re.S | re.I)


def parse_faction_techs(wikitext: str) -> tuple[list[dict], dict[str, str]]:
    """Faction techs plus the faction -> set map the unit-upgrade parser needs.

    Structure: ``== <edition> ==`` -> ``=== [[Faction]] ===`` -> one or more
    inline ``{{Tech|colour}} '''Name:''' text<blockquote>Prerequisites: ...``.

    The faction map is the load-bearing output: ``Unit Upgrade Technologies``
    carries no edition markers at all, so the only way to spot a Thunder's Edge
    upgrade there is the faction it belongs to.
    """
    techs: list[dict] = []
    faction_set: dict[str, str] = {}
    for sect_title, body in iter_sections(wikitext, 2):
        edition = section_edition(sect_title)
        if edition is None:
            continue
        for fac_title, fac_body in iter_sections(body, 3):
            faction = heading_name(fac_title)
            if not faction:
                continue
            faction_set[faction] = edition
            if edition in EXCLUDED_EDITIONS:
                continue
            techs.extend(_faction_techs_in(fac_body, faction, edition))
    if not faction_set:
        raise IngestError("Faction Technologies: no faction sections found")
    return techs, faction_set


def _faction_techs_in(body: str, faction: str, edition: str) -> list[dict]:
    out = []
    for m in _RE_FACTION_TECH.finditer(body):
        color = m.group("color").lower()
        if color not in TECH_COLORS:
            continue
        name = clean_text(m.group("name"))
        rest = m.group("rest")
        card_edition = find_edition(rest) or edition
        if card_edition in EXCLUDED_EDITIONS:
            continue
        pm = _RE_PREREQ_BLOCK.search(rest)
        prereqs = parse_prereqs(pm.group("p")) if pm else []
        # Card text is everything before the Prerequisites blockquote.
        text = clean_text(rest[: pm.start()] if pm else rest)
        if not text:
            raise IngestError(f"no card text for faction tech {name!r} ({faction})")
        display = make_name(name, text)
        out.append({
            "id": make_id(name, text),
            "name": display,
            "base_name": name.replace(_OMEGA, "").strip(),
            "color": color,
            "prerequisites": prereqs,
            "set": edition_to_set(card_edition),
            "faction": faction,
            "exhaustible": False,
            "text": text,
            "starting_for": [],
            "unit_upgrade": None,
        })
    return out


# --- family 3: Unit Upgrade Technologies -----------------------------------

# Stat labels the wiki uses, mapped to our field names. The label row is the
# authority: column sets differ per unit (a Destroyer has AFB, a Carrier has
# Capacity), so positions are meaningless and everything is matched by name.
STAT_LABELS = {
    "cost": "cost",
    "combat": "combat",
    "move": "move",
    "capacity": "capacity",
}

# Abilities named in the table's ability cell rather than as a stat column.
ABILITY_KEYWORDS = (
    "sustain damage", "anti-fighter barrage", "bombardment", "space cannon",
    "planetary shield", "production",
)

_RE_CELL_ATTRS = re.compile(r"^\s*([^|{}]*?=[^|]*)\|(.*)$", re.S)


def _cell_content(raw: str) -> str:
    """Strip a wikitable cell's optional `attributes |` prefix."""
    m = _RE_CELL_ATTRS.match(raw)
    return (m.group(2) if m else raw).strip()


def _table_rows(table: str) -> list[list[str]]:
    """Split a wikitable into rows of raw cell contents."""
    body = table.split("\n", 1)[1] if "\n" in table else ""
    # Drop the closing `|}`; it otherwise reads as a trailing cell containing "}"
    # and throws off the label-row/value-row length check.
    body = re.sub(r"\n\|\}\s*$", "\n", body)
    rows = []
    for chunk in re.split(r"^\|-.*$", body, flags=re.M):
        cells = [
            _cell_content(c)
            for c in re.findall(r"^[|!]([^\n]*)$", chunk, re.M)
            if c.strip() != ""
        ]
        if cells:
            rows.append(cells)
    return rows


def parse_unit_upgrades(wikitext: str, faction_set: dict[str, str]) -> list[dict]:
    """Unit upgrade techs, including faction variants.

    ``== [[Carrier]] ==`` sections, each holding one or more stat tables. This
    page carries **no edition markers at all**, so Thunder's Edge content is
    filtered via *faction_set* (built from Faction Technologies) — that is the
    only signal available here.
    """
    out = []
    for sect_title, body in iter_sections(wikitext, 2):
        unit = heading_name(sect_title)
        if unit in SKIP_SECTIONS:
            continue
        unit_key = slugify(unit)
        # Generic upgrade tables sit directly under the unit heading; faction
        # variants get their own h4 inside it. Both are parsed the same way, but
        # the faction is named only in the h4 heading, never inside the table, so
        # each table is paired with the heading text that precedes it.
        for m in _RE_TABLE.finditer(body):
            rec = _unit_upgrade_from_table(
                m.group(0), unit_key, faction_set, body[: m.start()]
            )
            if rec:
                out.append(rec)
    if not out:
        raise IngestError("Unit Upgrade Technologies: no upgrade tables found")
    return out


def _faction_before(preceding: str, faction_set: dict[str, str]) -> str | None:
    """The faction named in the nearest heading above a table, if any.

    Faction variants are headed `==== Advanced Carrier II ([[The Federation of
    Sol]]) ====`. Matching on the nearest preceding heading (not the whole
    section) keeps a variant from inheriting the faction of the one above it.
    """
    heads = list(_RE_HEADING.finditer(preceding))
    if not heads:
        return None
    last = heads[-1].group(2)
    for fac in faction_set:
        if fac.lower() in last.lower():
            return fac
    return None


def _unit_upgrade_from_table(
    table: str, unit_key: str, faction_set: dict[str, str], preceding: str
) -> dict | None:
    rows = _table_rows(table)
    if not rows or not rows[0]:
        return None
    name = clean_text(rows[0][0])
    if not name or name.lower().startswith("gallery"):
        return None

    faction = _faction_before(preceding, faction_set)
    if faction and faction_set.get(faction) in EXCLUDED_EDITIONS:
        return None  # the only Thunder's Edge filter available on this page

    stats, abilities = _unit_stats(rows)
    ability_text = _ability_text(rows)
    # PDS II and Space Dock II have NO stat table at all — only ability text. They
    # are still real techs, so absence of stats must not drop the card.
    if not stats and not ability_text:
        return None

    prereqs = parse_prereqs(table)  # no colour badge on this page: all are prereqs
    return {
        "id": slugify(name),
        "name": name,
        "base_name": name,
        "color": "unit_upgrade",
        "prerequisites": prereqs,
        "set": None,      # resolved by the caller from `faction`
        "faction": faction,
        "exhaustible": False,
        "text": _unit_text(name, unit_key, stats, abilities, ability_text),
        "starting_for": [],
        "unit_upgrade": {"unit": unit_key, **stats, "abilities": abilities},
    }


def _ability_text(rows: list[list[str]]) -> str:
    """The card's rules text, which sits in the cell beside the "Req." label."""
    for row in rows:
        for i, cell in enumerate(row):
            if clean_text(cell).lower().rstrip(".") == "req" and i + 1 < len(row):
                return clean_text(row[i + 1])
    return ""


def _unit_stats(rows: list[list[str]]) -> tuple[dict, list[str]]:
    """Read stats by matching the LABEL row, never by column position."""
    for i, row in enumerate(rows):
        labels = [clean_text(c).lower() for c in row]
        hits = [l for l in labels if l in STAT_LABELS]
        # One label is enough: Hel Titan II's table has only "Combat".
        if not hits or i == 0:
            continue
        values = rows[i - 1]
        if len(values) != len(row):
            continue
        stats = {}
        for label, value in zip(labels, values):
            key = STAT_LABELS.get(label)
            if not key:
                continue
            v = clean_text(value)
            if re.fullmatch(r"-?\d+", v):
                stats[key] = int(v)
        if stats:
            return stats, _abilities_in(rows)
    return {}, []


def _abilities_in(rows: list[list[str]]) -> list[str]:
    blob = clean_text(" ".join(c for row in rows for c in row)).lower()
    return [a for a in ABILITY_KEYWORDS if a in blob]


def _unit_text(
    name: str, unit_key: str, stats: dict, abilities: list[str], ability_text: str
) -> str:
    """The card's rules text plus its stats rendered as prose.

    The structured fields serve the calculator; this line serves RAG, which
    matches on text. Without spelling the numbers out, "what is Carrier II's
    capacity?" could never retrieve the chunk that knows the answer.
    """
    s = f"{name} is a {unit_key.replace('_', ' ')} unit upgrade."
    if stats:
        s += " Attributes: " + ", ".join(f"{k} {v}" for k, v in stats.items()) + "."
    if abilities:
        s += " Abilities: " + ", ".join(a.title() for a in abilities) + "."
    if ability_text:
        s += " " + ability_text.rstrip(".") + "."
    return s


# --- family 4: Discordant Stars faction techs ------------------------------

# `{{Tech|colour}} '''<big>Name</big>'''<blockquote>text</blockquote>
#  <blockquote>Prerequisites: {{Tech|colour}}...</blockquote>`
# Like the official faction format but with a <big>-wrapped name, no trailing
# colon, and the card text in its own blockquote. One record per match; a match
# runs until the next `{{Tech|colour}} '''`.
_RE_DS_TECH = re.compile(
    r"\{\{Tech\|(?P<color>\w+)\}\}\s*'''\s*<big>\s*(?P<name>.+?)\s*</big>\s*'''"
    r"(?P<rest>.*?)(?=\{\{Tech\|\w+\}\}\s*'''\s*<big>|\Z)",
    re.S,
)


def parse_ds_faction_techs(wikitext: str) -> list[dict]:
    """Discordant Stars faction technologies (a fan expansion; set='discordant_stars').

    Structure differs from the official page: ``== [[Faction]] ==`` directly (no
    edition sections), each holding inline ``{{Tech|colour}} '''<big>Name</big>'''``
    entries. DS carries no ``{{Edition}}`` templates at all.
    """
    out = []
    for sect_title, body in iter_sections(wikitext, 2):
        faction = heading_name(sect_title)
        if not faction or faction in SKIP_SECTIONS:
            continue
        for m in _RE_DS_TECH.finditer(body):
            color = m.group("color").lower()
            if color not in TECH_COLORS:
                continue
            name = clean_text(m.group("name"))
            rest = m.group("rest")
            pm = _RE_PREREQ_BLOCK.search(rest)
            prereqs = parse_prereqs(pm.group("p")) if pm else []
            text = clean_text(rest[: pm.start()] if pm else rest)
            if not name or not text:
                continue
            out.append({
                "id": slugify(name),
                "name": name,
                "base_name": name,
                "color": color,
                "prerequisites": prereqs,
                "set": "discordant_stars",
                "faction": faction,
                "exhaustible": False,
                "text": text,
                "starting_for": [],
                "unit_upgrade": None,
            })
    if not out:
        raise IngestError("DS Faction Technologies: no techs parsed")
    return out


# --- family 5: Discordant Stars faction-specific units ---------------------

def parse_ds_units(wikitext: str) -> list[dict]:
    """DS faction-specific unit upgrades, captured as RAG text only.

    Unlike the official unit-upgrade page, DS units are freeform ability prose
    with no Cost/Combat/Move/Capacity stat table, so no structured
    ``unit_upgrade`` stats are extracted — only the unit type (from the heading)
    and the card text. The Tactical Calculator work (later) can revisit these if
    it needs DS stats; for now they serve Rules Q&A grounding.
    """
    out = []
    for sect_title, body in iter_sections(wikitext, 2):
        heading = heading_name(sect_title)
        if not heading or heading in SKIP_SECTIONS:
            continue
        # Heading is "The Celdauri Trade Confederation ([[Space Dock]])".
        um = re.search(r"\(([^)]+)\)\s*$", heading)
        unit = slugify(clean_text(um.group(1))) if um else "unknown"
        faction = clean_text(re.sub(r"\s*\([^)]*\)\s*$", "", heading))
        for table in _RE_TABLE.findall(body):
            rec = _ds_unit_from_table(table, unit, faction)
            if rec:
                out.append(rec)
    return out


def _ds_unit_from_table(table: str, unit: str, faction: str) -> dict | None:
    rows = _table_rows(table)
    # Header row is Name | Abilities | Prerequisites; data rows follow.
    data = [r for r in rows if len(r) >= 2 and clean_text(r[0]).lower() != "name"]
    if not data:
        return None
    row = data[0]
    name = clean_text(re.sub(r"\(\[\[[^\]]+\]\]\)", "", row[0]))
    ability = clean_text(row[1]) if len(row) > 1 else ""
    if not name:
        return None
    prereqs = parse_prereqs(row[2]) if len(row) > 2 else parse_prereqs(table)
    text = f"{name} is a {unit.replace('_', ' ')} unit upgrade (Discordant Stars)."
    if ability:
        text += " " + ability.rstrip(".") + "."
    return {
        "id": slugify(name),
        "name": name,
        "base_name": name,
        "color": "unit_upgrade",
        "prerequisites": prereqs,
        "set": "discordant_stars",
        "faction": faction,
        "exhaustible": False,
        "text": text,
        "starting_for": [],
        "unit_upgrade": {"unit": unit, "abilities": []},
    }


# --- cross-cutting: starting techs and exhaustible flag --------------------

# A starting-tech cell names its techs one of two ways, depending on the page:
#   official: `{{Tech|colour}} [[Warfare Technologies#Magen...|Magen Defense Grid]]`
#   DS:       `{{Tech|colour|Sarween Tools}}`  (name is the template's 2nd arg)
_RE_START_LINK = re.compile(r"\{\{Tech\|\w+\}\}\s*\[\[[^\]|]+(?:\|([^\]]+))?\]\]")
_RE_START_TMPL = re.compile(r"\{\{Tech\|\w+\|([^}]+)\}\}")


def _starting_techs_in_cell(cell: str) -> list[str]:
    """Tech names named in a starting-tech cell, in either page's format."""
    names = [m for m in _RE_START_LINK.findall(cell) if m]
    if names:
        return [clean_text(n) for n in names]
    # DS form: the 2nd template arg is the tech name (not `w=32px`, not a colour).
    out = []
    for arg in _RE_START_TMPL.findall(cell):
        name = clean_text(arg)
        if name and "=" not in arg and name.lower() not in TECH_COLORS:
            out.append(name)
    return out


def parse_starting_tech(wikitext: str) -> dict[str, list[str]]:
    """Map ``tech name -> [faction display names]`` from a Starting Technology page.

    Both the official and DS starting pages share a two-column table (faction |
    tech links), with a faction spanning multiple rows via ``rowspan``. We track
    the current faction as we walk cells and attribute each tech link to it.
    """
    starting: dict[str, list[str]] = {}
    table_m = _RE_TABLE.search(wikitext)
    if not table_m:
        raise IngestError("Starting Technology: no table found")
    current = None
    for row in _table_rows(table_m.group(0)):
        for cell in row:
            fac = _RE_WIKILINK.match(cell.strip())
            techs = _starting_techs_in_cell(cell)
            if techs:
                for name in techs:
                    if name and current:
                        starting.setdefault(name, [])
                        if current not in starting[name]:
                            starting[name].append(current)
            elif fac and "{{Tech" not in cell and "Edition" not in cell and "Technolog" not in cell:
                # A faction cell (a plain wikilink, no {{Tech}}); becomes current.
                name = clean_text(cell)
                if name and name not in ("Faction",):
                    current = name
    return starting


def parse_exhaustible_names(wikitext: str) -> set[str]:
    """The set of tech names flagged exhaustible.

    Only the names are needed: the flag is applied to records already built from
    the other pages. A name that matches nothing (e.g. a Thunder's Edge tech we
    excluded) is simply never applied, so this is safe without edition filtering.
    """
    names = set()
    for table in _RE_TABLE.findall(wikitext):
        name = _table_title(table)
        if name:
            names.add(name)
    return names


# The tech name a card table announces, however the header is written:
#   name on the line after the `!` row (colour pages), or
#   name on the `!` line itself after a style attribute (Exhaustible page).
_RE_HEADER_INLINE = re.compile(r"^!.*?;\s*\|\s*(?P<n>[^|\n]+?)\s*$", re.M)


def _table_title(table: str) -> str:
    hm = _RE_TABLE_HEADER.search(table)
    if hm and clean_text(hm.group("h")):
        return clean_text(hm.group("h"))
    im = _RE_HEADER_INLINE.search(table)
    if im and clean_text(im.group("n")):
        return clean_text(im.group("n"))
    return ""



# --- assembly --------------------------------------------------------------

# The page allowlist. Explicit on purpose: `Category:Technologies` also contains
# First/Second/Third Edition pages, and walking the category would silently
# ingest wrong-edition tech. Each entry maps a page to the parser family it uses.
COLOR_PAGES = {
    "Biotic Technologies": "biotic",
    "Cybernetic Technologies": "cybernetic",
    "Propulsion Technologies": "propulsion",
    "Warfare Technologies": "warfare",
}
FACTION_PAGE = "Faction Technologies"
UNIT_UPGRADE_PAGE = "Unit Upgrade Technologies"
STARTING_PAGES = ["Starting Technology", "Discordant Stars Starting Technology (UNOFFICIAL)"]
EXHAUSTIBLE_PAGE = "Exhaustible Technologies"
DS_FACTION_PAGE = "Discordant Stars Faction Technologies (UNOFFICIAL)"
DS_UNITS_PAGE = "Discordant Stars Faction Specific Units (UNOFFICIAL)"


def _resolve_unit_upgrade_set(rec: dict, official_faction_set: dict[str, str]) -> str:
    """A unit upgrade's `set`: its faction's edition, or `base` for generic ones.

    The nine generic upgrades (Carrier II, ...) are all base game; faction
    variants inherit their faction's set. This page carries no edition markers,
    so a faction is the only signal available.
    """
    if rec["faction"] and rec["faction"] in official_faction_set:
        return edition_to_set(official_faction_set[rec["faction"]])
    return "base"


def build_corpus(fetch) -> dict:
    """Assemble the full technology corpus. *fetch* maps a page name to wikitext.

    Injecting *fetch* (rather than calling the network here) keeps assembly
    testable offline against committed wikitext fixtures.
    """
    records: list[dict] = []

    # Faction techs first: they yield the faction -> edition map the unit-upgrade
    # page needs (it has no edition markers of its own).
    faction_techs, official_faction_set = parse_faction_techs(fetch(FACTION_PAGE))
    records.extend(faction_techs)

    for page, color in COLOR_PAGES.items():
        records.extend(parse_color_page(fetch(page), color))

    for rec in parse_unit_upgrades(fetch(UNIT_UPGRADE_PAGE), official_faction_set):
        rec["set"] = _resolve_unit_upgrade_set(rec, official_faction_set)
        records.append(rec)

    records.extend(parse_ds_faction_techs(fetch(DS_FACTION_PAGE)))
    records.extend(parse_ds_units(fetch(DS_UNITS_PAGE)))

    # Cross-cutting flags applied to the records built above.
    exhaustible = parse_exhaustible_names(fetch(EXHAUSTIBLE_PAGE))
    starting: dict[str, list[str]] = {}
    for page in STARTING_PAGES:
        for name, factions in parse_starting_tech(fetch(page)).items():
            starting.setdefault(name, [])
            for f in factions:
                if f not in starting[name]:
                    starting[name].append(f)

    by_name = {r["name"]: r for r in records}
    for name in exhaustible:
        if name in by_name:
            by_name[name]["exhaustible"] = True
    for name, factions in starting.items():
        if name in by_name:
            by_name[name]["starting_for"] = factions

    validate(records)
    return {
        "provenance": {
            "source": "Twilight Imperium 4E technology cards, transcribed via the TI4 Fandom wiki",
            "source_url": "https://twilight-imperium.fandom.com/wiki/Technology",
            "retrieved": datetime.date.today().isoformat(),
            "pages": sorted(
                [FACTION_PAGE, UNIT_UPGRADE_PAGE, EXHAUSTIBLE_PAGE, DS_FACTION_PAGE, DS_UNITS_PAGE]
                + list(COLOR_PAGES) + STARTING_PAGES
            ),
            "excluded_editions": sorted(EXCLUDED_EDITIONS),
            "wiki_license": "Fandom content is CC-BY-SA 3.0; attribution retained here.",
            "ip_note": (
                "Mechanical card text is Fantasy Flight Games / Asmodee IP, vendored for a "
                "free fan tool (RAG grounding / citations), same basis as the LRR corpus."
            ),
            "unofficial_note": (
                "Discordant Stars is a fan-made expansion, not FFG-published; its entries "
                "carry set='discordant_stars'."
            ),
        },
        "technologies": sorted(records, key=lambda r: (r["color"], r["name"])),
    }


# --- validation ------------------------------------------------------------

VALID_SETS = frozenset({
    "base", "pok", "keleres", "codex_i", "codex_ii", "codex_iv", "discordant_stars",
})
VALID_COLORS = frozenset(TECH_COLORS | {"unit_upgrade"})


def validate(records: list[dict]) -> None:
    """Fail loudly on structural problems a fan-sourced corpus is prone to.

    This is the accuracy gate: a Thunder's Edge leak, a duplicate, a phantom
    prerequisite, or an earlier-edition name shows up here rather than as a bad
    answer in production.
    """
    if not records:
        raise IngestError("empty corpus")

    problems: list[str] = []

    # Duplicate ids (id is the citation key; a collision would merge two techs).
    seen: dict[str, dict] = {}
    for r in records:
        if r["id"] in seen and r["text"] != seen[r["id"]]["text"]:
            problems.append(f"duplicate id {r['id']!r} with differing text")
        seen[r["id"]] = r

    for r in records:
        if r["set"] not in VALID_SETS:
            problems.append(f"{r['name']!r}: unexpected set {r['set']!r}")
        if r["color"] not in VALID_COLORS:
            problems.append(f"{r['name']!r}: unexpected color {r['color']!r}")
        if not r["text"].strip():
            problems.append(f"{r['name']!r}: empty text")
        # A non-unit-upgrade tech has 0-3 single-colour prerequisites. More than
        # 3 is the phantom-prereq bug (badge miscounted as a prerequisite).
        if r["color"] in TECH_COLORS and len(r["prerequisites"]) > 3:
            problems.append(f"{r['name']!r}: {len(r['prerequisites'])} prerequisites (>3)")
        for p in r["prerequisites"]:
            if p not in TECH_COLORS:
                problems.append(f"{r['name']!r}: bad prerequisite colour {p!r}")

    # No Thunder's Edge leak: every unit-upgrade faction must be one the official
    # faction page knew, and none excluded. (DS units carry DS factions, skipped.)
    if problems:
        raise IngestError("corpus validation failed:\n  - " + "\n  - ".join(problems))


# --- CLI -------------------------------------------------------------------

DEFAULT_OUT = "core/data/source/tech/ti_technologies.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT, help="corpus JSON output path")
    parser.add_argument(
        "--cache-dir",
        help="reuse/store fetched wikitext here (avoids re-hitting the wiki on reruns)",
    )
    args = parser.parse_args(argv)

    cache = Path(args.cache_dir) if args.cache_dir else None
    corpus = build_corpus(lambda page: fetch_wikitext(page, cache))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    techs = corpus["technologies"]
    by_set: dict[str, int] = {}
    for t in techs:
        by_set[t["set"]] = by_set.get(t["set"], 0) + 1
    print(f"Wrote {len(techs)} technologies to {out}")
    print("  by set:", ", ".join(f"{k}={v}" for k, v in sorted(by_set.items())))
    print("  unit upgrades:", sum(1 for t in techs if t["unit_upgrade"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
