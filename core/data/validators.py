"""Data-correctness validators for Oracle Rex board data.

The runtime source of truth for board data lives in ``core/util/default_data/``
(``DEFAULT_PLANETS`` / ``DEFAULT_SYSTEMS`` / ``DEFAULT_FACTIONS``), which
``reset_database()`` loads on every session start. The canonical *reference*
data is the Milty Draft export copied into ``core/data/source/``.

These validators answer four questions:

1. **Internal consistency** -- does the default data agree with the model
   constants and reference itself correctly? (no orphan planets, unique tile
   ids, valid wormhole/anomaly/trait/tech values, present resources/influence)
2. **Source parity** -- does every Milty Draft tile/planet/faction match
   Oracle Rex's data once the naming-convention mapping is applied?
3. **Graphic assets** -- does each system tile have a matching board image
   (``static/images/systems/ST_<tile_id>.png``)?
4. **TTS parser** -- is the TTS-string parser configuration consistent with the
   data it resolves against?

Each ``validate_*`` function returns a ``list[str]`` of human-readable problems
(empty == clean). ``run_all_validations()`` aggregates them into an ordered
dict keyed by check name. Run locally via ``python manage.py validate_data``.

Milty Draft uses different field names/values for the same data; those
differences are intentional and preserved. The mappings applied when comparing
against the source are:

* planet ``specialty`` -> ``tech_specialty``:
  ``biotic->green``, ``propulsion->blue``, ``cybernetic->yellow``,
  ``warfare->red``, ``null->none``
* ``trait`` / ``anomaly`` ``null`` -> ``"none"``
* ``legendary`` (Milty stores the ability text or ``false``) -> boolean
* Milty source typos are normalised before comparison: a stray trailing
  apostrophe on ``trait`` (e.g. ``"industrial'"``) and spaces in ``anomaly``
  (e.g. ``"gravity rift"``) -- Oracle Rex stores the clean enum value.
"""

import json
import re
from collections import OrderedDict
from pathlib import Path

from core.models.constants.anomalyConstants import AnomalyType
from core.models.constants.planetConstants import PlanetTechs, PlanetTraits
from core.models.constants.wormholeConstants import WormholeType
from core.util.default_data.default_factions import DEFAULT_FACTIONS
from core.util.default_data.default_planets import DEFAULT_PLANETS
from core.util.default_data.default_systems import DEFAULT_SYSTEMS

# --- paths -----------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent
SOURCE_TILES_PATH = _DATA_DIR / "source" / "milty_draft_tiles.json"
SOURCE_FACTIONS_PATH = _DATA_DIR / "source" / "milty_draft_factions.json"
# Vendored Living Rules Reference corpus for grounded Rules Q&A (produced by
# scripts/ingest_lrr.py from the official LRR PDF; the PDF is not committed).
LRR_RULES_PATH = _DATA_DIR / "source" / "lrr" / "lrr_rules.json"
# core/ -> project root -> static/images/systems
SYSTEM_IMAGE_DIR = _DATA_DIR.parent.parent / "static" / "images" / "systems"

# --- naming-convention mapping (Milty Draft -> Oracle Rex) -----------------

SPECIALTY_TO_TECH = {
    "biotic": "green",
    "propulsion": "blue",
    "cybernetic": "yellow",
    "warfare": "red",
    None: "none",
}

# System tiles that have no dedicated board graphic. These are anomaly "back"
# tiles for Discordant Stars factions (Zelian / Myko); they are not selectable
# in a standard TTS draft string, so a missing image is a known, accepted gap
# rather than a board-rendering bug.
KNOWN_MISSING_IMAGE_TILE_IDS = {4224, 4225}

VALID_TRAITS = set(PlanetTraits.values)
VALID_TECHS = set(PlanetTechs.values)
VALID_WORMHOLES = set(WormholeType.values)
VALID_ANOMALIES = set(AnomalyType.values)


# --- helpers ---------------------------------------------------------------

def _load_source_tiles():
    with open(SOURCE_TILES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _load_source_factions():
    with open(SOURCE_FACTIONS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _norm_source_trait(trait):
    """Milty trait -> Oracle trait (handles null and stray apostrophe typos)."""
    if trait is None:
        return "none"
    return trait.strip().strip("'")


def _norm_source_anomaly(anomaly):
    """Milty anomaly -> Oracle anomaly (handles null and spaced typos)."""
    if anomaly is None:
        return "none"
    return anomaly.strip().replace(" ", "-")


def _source_anomalies_for_tile(tile_data):
    """Collect the anomaly(ies) a Milty tile declares (tile- and planet-level)."""
    anomalies = set()
    if tile_data.get("anomaly"):
        anomalies.add(_norm_source_anomaly(tile_data["anomaly"]))
    for planet in tile_data.get("planets", []):
        if planet.get("anomaly"):
            anomalies.add(_norm_source_anomaly(planet["anomaly"]))
    return anomalies


def _planets_by_name():
    return {p["name"]: p for p in DEFAULT_PLANETS}


def _systems_by_tile_id():
    return {str(s["tile_id"]): s for s in DEFAULT_SYSTEMS}


# --- validators ------------------------------------------------------------

def validate_internal_consistency():
    """Default data agrees with model constants and references itself correctly."""
    issues = []
    planets = _planets_by_name()

    # Duplicate planet names.
    seen = set()
    for p in DEFAULT_PLANETS:
        if p["name"] in seen:
            issues.append(f"Duplicate planet name in DEFAULT_PLANETS: {p['name']!r}")
        seen.add(p["name"])

    # Per-planet field validity.
    for p in DEFAULT_PLANETS:
        name = p["name"]
        for field in ("resources", "influence"):
            value = p.get(field)
            if not isinstance(value, int) or value < 0:
                issues.append(f"Planet {name!r} has missing/invalid {field}: {value!r}")
        if p.get("trait", "none") not in VALID_TRAITS:
            issues.append(f"Planet {name!r} has invalid trait: {p.get('trait')!r}")
        if p.get("tech_specialty", "none") not in VALID_TECHS:
            issues.append(f"Planet {name!r} has invalid tech_specialty: {p.get('tech_specialty')!r}")

    # Duplicate system tile ids.
    seen_ids = set()
    for s in DEFAULT_SYSTEMS:
        tid = s["tile_id"]
        if tid in seen_ids:
            issues.append(f"Duplicate system tile_id: {tid}")
        seen_ids.add(tid)

    # System field validity + planet references.
    planet_to_systems = {}
    for s in DEFAULT_SYSTEMS:
        tid = s["tile_id"]
        if s.get("wormhole", "none") not in VALID_WORMHOLES:
            issues.append(f"System {tid} ({s['name']}) has invalid wormhole: {s.get('wormhole')!r}")
        if s.get("anomaly", "none") not in VALID_ANOMALIES:
            issues.append(f"System {tid} ({s['name']}) has invalid anomaly: {s.get('anomaly')!r}")
        for planet_name in s["planets"]:
            if planet_name not in planets:
                issues.append(
                    f"System {tid} ({s['name']}) references nonexistent planet: {planet_name!r}"
                )
            planet_to_systems.setdefault(planet_name, []).append(tid)

    # Orphan planets (defined but not placed in any system) and planets placed
    # in more than one system.
    for name in planets:
        homes = planet_to_systems.get(name, [])
        if not homes:
            issues.append(f"Planet {name!r} is not referenced by any system (orphan)")
        elif len(homes) > 1:
            issues.append(f"Planet {name!r} is referenced by multiple systems: {homes}")

    # Faction home systems must exist.
    system_names = {s["name"] for s in DEFAULT_SYSTEMS}
    for f in DEFAULT_FACTIONS:
        if f["home_system"] not in system_names:
            issues.append(
                f"Faction {f['name']!r} references nonexistent home system: {f['home_system']!r}"
            )

    return issues


def validate_against_milty_source():
    """Oracle Rex data matches the canonical Milty Draft export."""
    issues = []
    source = _load_source_tiles()
    planets = _planets_by_name()
    systems = _systems_by_tile_id()

    source_planet_names = set()

    for tid, tdata in source.items():
        if tdata.get("type") == "hyperlane":
            continue  # Oracle Rex does not model hyperlane tiles.
        if tid not in systems:
            issues.append(f"Tile {tid} ({tdata.get('faction', '')}) in Milty source but missing from Oracle systems")
            continue
        sys_o = systems[tid]

        # Wormhole.
        m_worm = tdata.get("wormhole") or "none"
        if sys_o["wormhole"] != m_worm:
            issues.append(f"Tile {tid} wormhole: Oracle={sys_o['wormhole']!r} Milty={m_worm!r}")

        # Anomaly.
        m_anoms = _source_anomalies_for_tile(tdata)
        if m_anoms:
            if sys_o["anomaly"] not in m_anoms:
                issues.append(f"Tile {tid} anomaly: Oracle={sys_o['anomaly']!r} Milty={sorted(m_anoms)}")
        elif sys_o["anomaly"] != "none":
            issues.append(f"Tile {tid} anomaly: Oracle={sys_o['anomaly']!r} Milty=none")

        # Planet membership.
        m_names = [p["name"] for p in tdata.get("planets", [])]
        if sorted(m_names) != sorted(sys_o["planets"]):
            issues.append(f"Tile {tid} planet set: Oracle={sys_o['planets']} Milty={m_names}")

        # Per-planet attributes.
        for mp in tdata.get("planets", []):
            name = mp["name"]
            source_planet_names.add(name)
            if name not in planets:
                issues.append(f"Planet {name!r} (tile {tid}) in Milty source but missing from Oracle planets")
                continue
            op = planets[name]
            if op["resources"] != mp["resources"]:
                issues.append(f"Planet {name!r} resources: Oracle={op['resources']} Milty={mp['resources']}")
            if op["influence"] != mp["influence"]:
                issues.append(f"Planet {name!r} influence: Oracle={op['influence']} Milty={mp['influence']}")
            m_trait = _norm_source_trait(mp.get("trait"))
            if op["trait"] != m_trait:
                issues.append(f"Planet {name!r} trait: Oracle={op['trait']!r} Milty={m_trait!r}")
            m_spec = SPECIALTY_TO_TECH.get(mp.get("specialty"), f"<unmapped:{mp.get('specialty')!r}>")
            if op["tech_specialty"] != m_spec:
                issues.append(
                    f"Planet {name!r} tech_specialty: Oracle={op['tech_specialty']!r} "
                    f"Milty specialty={mp.get('specialty')!r} -> {m_spec!r}"
                )
            m_legendary = bool(mp.get("legendary"))
            if bool(op.get("legendary", False)) != m_legendary:
                issues.append(
                    f"Planet {name!r} legendary: Oracle={bool(op.get('legendary', False))} Milty={m_legendary}"
                )

    # Oracle planets that do not exist in the source.
    for name in planets:
        if name not in source_planet_names:
            issues.append(f"Oracle planet {name!r} not present in Milty source")

    return issues


def validate_factions_against_milty_source():
    """Oracle faction ids and home tiles match the Milty Draft source."""
    issues = []
    source_factions = _load_source_factions()
    source_tiles = _load_source_tiles()
    systems = _systems_by_tile_id()
    sys_name_to_tile = {s["name"]: str(s["tile_id"]) for s in DEFAULT_SYSTEMS}

    milty_ids = {f["id"] for f in source_factions.values()}
    # Home tile per faction id, resolved from tiles.json `faction` field (the
    # factions.json `homesystem` is 0 for Discordant factions).
    fullname_to_tile = {
        td["faction"]: tid for tid, td in source_tiles.items() if "faction" in td
    }
    milty_id_to_tile = {}
    for f in source_factions.values():
        milty_id_to_tile[f["id"]] = fullname_to_tile.get(f["name"], f.get("homesystem"))

    # The Council Keleres has no fixed home tile (homesystem 0); Oracle Rex
    # does not model it, so it is excluded from the parity check.
    skip_ids = {"keleres"}

    for f in DEFAULT_FACTIONS:
        name = f["name"]
        if name not in milty_ids:
            issues.append(f"Oracle faction id {name!r} not found in Milty source faction ids")
            continue
        o_tile = sys_name_to_tile.get(f["home_system"])
        m_tile = milty_id_to_tile.get(name)
        # m_tile may be "0" for factions Milty does not pin to a tile; only flag
        # when Milty actually pins a tile and it disagrees.
        if m_tile and m_tile != "0" and o_tile != m_tile:
            issues.append(
                f"Faction {name!r} home tile: Oracle={o_tile} ({f['home_system']}) Milty={m_tile}"
            )

    for fid in sorted(milty_ids - {f["name"] for f in DEFAULT_FACTIONS} - skip_ids):
        issues.append(f"Milty faction id {fid!r} has no matching Oracle faction")

    return issues


def validate_tile_images():
    """Every system tile has a matching board graphic (or is a known gap)."""
    issues = []
    for s in DEFAULT_SYSTEMS:
        tid = s["tile_id"]
        if tid in KNOWN_MISSING_IMAGE_TILE_IDS:
            continue
        image_path = SYSTEM_IMAGE_DIR / f"ST_{tid}.png"
        if not image_path.exists():
            issues.append(f"System {tid} ({s['name']}) has no board image: ST_{tid}.png")
    return issues


def validate_tts_parser_config():
    """The TTS-string parser config is consistent with the system data."""
    issues = []
    # Imported lazily so this module stays importable without numpy at module load.
    from core.service.tts_string_ingest import HOME_SYSTEM_IDS, MAX_ID_NUM

    systems = _systems_by_tile_id()
    source_tiles = _load_source_tiles()
    faction_tiles = {tid for tid, td in source_tiles.items() if "faction" in td}

    # Every id the parser accepts as a home system must resolve to a system that
    # is actually a faction home in the source data.
    for hid in HOME_SYSTEM_IDS:
        sid = str(int(hid))
        if sid not in systems:
            issues.append(f"TTS parser home id {sid} has no system in default data")
            continue
        if sid not in faction_tiles:
            issues.append(f"TTS parser home id {sid} is not a faction home tile in Milty source")

    # Every faction home tile in the source should be accepted by the parser,
    # so a real draft string never trips the home-system check.
    home_ids = {str(int(h)) for h in HOME_SYSTEM_IDS}
    for tid in sorted(faction_tiles, key=int):
        if int(tid) > MAX_ID_NUM:
            issues.append(f"Faction home tile {tid} exceeds parser MAX_ID_NUM {MAX_ID_NUM}")
        if tid not in home_ids:
            issues.append(f"Faction home tile {tid} is not accepted by the TTS parser HOME_SYSTEM_IDS")

    # Every system tile id the parser could resolve must be <= MAX_ID_NUM.
    for s in DEFAULT_SYSTEMS:
        if s["tile_id"] > MAX_ID_NUM:
            issues.append(f"System tile_id {s['tile_id']} exceeds parser MAX_ID_NUM {MAX_ID_NUM}")

    return issues


_RULE_ID_RE = re.compile(r"^\d+(\.\d+)?$")


def _norm_topic_name(name):
    """Topic name -> comparison key: lowercased, parenthetical qualifier dropped
    (so a related reference "Combat" resolves to the topic "Combat (Attribute)").
    """
    return name.split("(")[0].strip().lower()


def validate_lrr_corpus():
    """The vendored Living Rules Reference corpus is well-formed.

    Checks (all cheap, all deterministic): the file loads with provenance; every
    chunk has a valid, unique ``rule_id`` and non-empty ``text``; every sub-rule
    points at a topic that exists; and every ``related`` reference resolves to a
    known topic name. Numbering need not be contiguous -- the LRR itself skips
    numbers (e.g. Movement has no 58.1) and renders a few sub-rules next to
    ALL-CAPS sub-headers, so gaps are faithful, not errors.
    """
    issues = []
    if not LRR_RULES_PATH.exists():
        return [f"LRR corpus missing: {LRR_RULES_PATH} (run scripts/ingest_lrr.py)"]

    with open(LRR_RULES_PATH, encoding="utf-8") as fh:
        data = json.load(fh)

    prov = data.get("provenance") or {}
    for field in ("source", "version", "source_url"):
        if not prov.get(field):
            issues.append(f"LRR provenance missing {field!r}")

    chunks = data.get("chunks") or []
    if not chunks:
        return issues + ["LRR corpus has no chunks"]

    topic_ids = {c["rule_id"] for c in chunks if c.get("kind") == "topic_intro"}
    topic_names = {_norm_topic_name(c["topic"]) for c in chunks if c.get("kind") == "topic_intro"}

    seen = set()
    for c in chunks:
        rid = c.get("rule_id", "")
        if not _RULE_ID_RE.match(str(rid)):
            issues.append(f"LRR chunk has invalid rule_id: {rid!r}")
        if rid in seen:
            issues.append(f"LRR duplicate rule_id: {rid!r}")
        seen.add(rid)
        if not (c.get("text") or "").strip():
            issues.append(f"LRR chunk {rid!r} has empty text")
        if not (c.get("topic") or "").strip():
            issues.append(f"LRR chunk {rid!r} has empty topic")
        # Sub-rules must point at a real topic intro.
        if c.get("kind") == "rule":
            parent = c.get("parent_topic")
            if parent not in topic_ids:
                issues.append(f"LRR sub-rule {rid!r} has unknown parent_topic {parent!r}")
        # Every related reference must resolve to a known topic name.
        for ref in c.get("related") or []:
            if _norm_topic_name(ref) not in topic_names:
                issues.append(f"LRR topic {rid!r} has unresolved related reference: {ref!r}")

    return issues


def run_all_validations():
    """Run every validator. Returns an ordered ``{check_name: [issues]}`` dict."""
    return OrderedDict(
        [
            ("internal_consistency", validate_internal_consistency()),
            ("milty_source_parity", validate_against_milty_source()),
            ("faction_source_parity", validate_factions_against_milty_source()),
            ("tile_images", validate_tile_images()),
            ("tts_parser_config", validate_tts_parser_config()),
            ("lrr_corpus", validate_lrr_corpus()),
        ]
    )
