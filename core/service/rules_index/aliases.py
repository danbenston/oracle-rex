"""TI jargon -> rules-vocabulary aliases, applied at query time.

Lexical retrieval's main failure mode for a rules bot is jargon: players type
"PDS" / "taccy" / "cap", but the Living Rules Reference says "space cannon" /
"tactical action" / "capacity". This map expands recognized shorthand in the
query into the terms the corpus actually uses, so the FTS5 match lands on the
right rules. It is deliberately small, curated, and testable — the codegraft
discipline: cheap lexical fixes before reaching for embeddings.

Keys are matched case-insensitively against whole query tokens (and a few
two-word phrases). Values are extra terms OR'd into the FTS query; the original
token is kept too, so an alias only ever *adds* recall.
"""

from __future__ import annotations

# Single-token jargon -> extra query terms.
ALIASES: dict[str, list[str]] = {
    # Structures / abilities
    "pds": ["space", "cannon", "structure"],
    "sco": ["space", "cannon", "offense"],
    "scd": ["space", "cannon", "defense"],
    "afb": ["anti-fighter", "barrage"],
    "sustain": ["sustain", "damage"],
    # Actions
    "taccy": ["tactical", "action"],
    "tac": ["tactical", "action"],
    "strat": ["strategic", "action", "strategy", "card"],
    # Transport vocabulary: the LRR describes carrying ground forces as
    # "transport" and only ever "pick up"; players say "drop off" / "unload",
    # which share no tokens with the rules text (rule 95 Transport, 95.1a). Map
    # that jargon onto the terms the corpus actually uses so both directions
    # retrieve the transport rule.
    "drop": ["transport", "active"],
    "dropoff": ["transport", "active"],
    "unload": ["transport", "active"],
    "disembark": ["transport", "active"],
    "deposit": ["transport", "active"],
    # Resources / economy
    "cap": ["capacity"],
    "tg": ["trade", "good"],
    "tgs": ["trade", "goods"],
    "ct": ["command", "token"],
    "cts": ["command", "tokens"],
    "vp": ["victory", "point"],
    "vps": ["victory", "points"],
    "prod": ["production"],
    # Board / places
    "mr": ["mecatol", "rex"],
    "mecatol": ["mecatol", "rex"],
    # Units
    "dread": ["dreadnought"],
    "inf": ["infantry"],
    "ff": ["fighter"],
    "gf": ["ground", "forces"],
    "gfs": ["ground", "forces"],
}

# Multi-word phrases -> extra terms (checked against adjacent token pairs).
PHRASE_ALIASES: dict[str, list[str]] = {
    "space cannon": ["space", "cannon", "offense", "defense"],
    "home system": ["home", "system"],
    "action phase": ["action", "phase"],
    # "drop off" / "pick up" -> the transport rule's vocabulary.
    "drop off": ["transport", "active", "system", "commit"],
    "pick up": ["transport"],
}


def expand_terms(tokens: list[str]) -> list[str]:
    """Return ``tokens`` plus any alias expansions, de-duplicated in order."""
    out: list[str] = list(tokens)

    for tok in tokens:
        for extra in ALIASES.get(tok, ()):
            out.append(extra)

    for a, b in zip(tokens, tokens[1:]):
        phrase = f"{a} {b}"
        for extra in PHRASE_ALIASES.get(phrase, ()):
            out.append(extra)

    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped
