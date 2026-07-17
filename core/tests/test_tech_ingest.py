"""Offline tests for the technology corpus ingest (scripts/ingest_tech.py).

Phase 1 of .features/technology_corpus.md. These never touch the network: the
corpus is assembled from committed wikitext fixtures in
``core/tests/fixtures/tech_wiki/`` (the exact pages pulled during ingest), so the
tricky parsing is pinned against real wiki markup.

Why so much of this is about *parsing* rather than data: the TI4 wiki has five
distinct page formats and several traps that silently produce wrong data rather
than errors — Codex "Omega" card revisions that share a name, prerequisite
counts confused by the card's own colour badge, and Thunder's Edge content
(out of scope) interleaved with no edition marker on the unit-upgrade page. Each
of those has a dedicated test here because each was a real bug during ingest.
"""

import sys
from pathlib import Path

from django.test import SimpleTestCase

# The ingest script lives in scripts/ (not an importable package), so add it to
# the path the same way scripts/check_model_availability.py is reached in CI.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import ingest_tech as T  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tech_wiki"


def _fixture_fetch(page: str) -> str:
    """A `fetch` for build_corpus that reads the committed wikitext fixtures."""
    return (_FIXTURES / (T.slugify(page) + ".wikitext")).read_text(encoding="utf-8")


class TechCorpusBuildTest(SimpleTestCase):
    """The assembled corpus, built once from fixtures."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corpus = T.build_corpus(_fixture_fetch)
        cls.techs = cls.corpus["technologies"]
        cls.by_name = {t["name"]: t for t in cls.techs}
        cls.by_id = {t["id"]: t for t in cls.techs}

    def test_builds_a_nonempty_corpus_that_passes_validation(self):
        # build_corpus calls validate() internally; reaching here means it passed.
        self.assertGreater(len(self.techs), 140)

    def test_ids_are_unique(self):
        ids = [t["id"] for t in self.techs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_record_has_the_full_schema(self):
        required = {
            "id", "name", "color", "prerequisites", "set",
            "faction", "exhaustible", "text", "starting_for", "unit_upgrade",
        }
        for t in self.techs:
            self.assertTrue(required.issubset(t), f"{t['name']} missing keys")
            self.assertTrue(t["text"].strip(), f"{t['name']} has empty text")

    def test_sets_are_all_in_scope(self):
        # base + PoK + Codex + DS only. A stray edition here is a scope leak.
        for t in self.techs:
            self.assertIn(t["set"], T.VALID_SETS, f"{t['name']} set={t['set']}")

    # --- Thunder's Edge exclusion (the scope decision) ---------------------

    def test_no_thunders_edge_content(self):
        # TE leaks three ways; assert none of its cards or factions survived.
        te_cards = {"Radical Advancement", "Hydrothermal Mining"}
        self.assertFalse(te_cards & set(self.by_name))
        te_factions = {"The Deepwrought Scholarate", "Last Bastion",
                       "The Crimson Rebellion", "The Ral Nel Consortium"}
        for t in self.techs:
            self.assertNotIn(t["faction"], te_factions, f"TE leak via {t['name']}")

    def test_te_only_unit_upgrade_excluded(self):
        # 4x41C "Helios" V2 is a Last Bastion (TE) space-dock upgrade with no
        # edition marker; it can only be filtered by its faction.
        self.assertNotIn('4x41c_helios_v2', self.by_id)

    # --- prerequisites (the badge trap) ------------------------------------

    def test_base_prerequisite_ladder_is_exact(self):
        # Each colour has base techs at 0/1/2/3 prerequisites. This ladder is the
        # canary for the badge-vs-prerequisite miscount: an off-by-one here means
        # a card's own colour icon was counted (or a real prereq was dropped).
        self.assertEqual(self.by_name["Neural Motivator"]["prerequisites"], [])
        self.assertEqual(self.by_name["Dacxive Animators"]["prerequisites"], ["biotic"])
        self.assertEqual(
            self.by_name["Hyper Metabolism"]["prerequisites"], ["biotic", "biotic"]
        )
        self.assertEqual(
            self.by_name["X-89 Bacterial Weapon"]["prerequisites"],
            ["biotic", "biotic", "biotic"],
        )

    def test_prereq_written_like_a_badge_is_still_counted(self):
        # Self Assembly Routines writes its lone prerequisite as
        # {{Tech|warfare|w=32px}} — identical to a colour badge. The positional
        # rule must still count it; the naive `w=`-means-badge rule dropped it.
        self.assertEqual(
            self.by_name["Self Assembly Routines"]["prerequisites"], ["warfare"]
        )

    def test_no_tech_exceeds_three_prerequisites(self):
        for t in self.techs:
            if t["color"] in T.TECH_COLORS:
                self.assertLessEqual(len(t["prerequisites"]), 3, t["name"])

    # --- Omega revisions (the slug-collision trap) -------------------------

    def test_omega_revisions_are_distinct_records(self):
        # Colour-page Omega: Ω is in the card name.
        for name in ("X-89 Bacterial Weapon", "X-89 Bacterial Weapon Ω",
                     "X-89 Bacterial Weapon ΩΩ"):
            self.assertIn(name, self.by_name)
        self.assertEqual(self.by_name["X-89 Bacterial Weapon Ω"]["set"], "codex_i")
        self.assertEqual(self.by_name["X-89 Bacterial Weapon ΩΩ"]["set"], "codex_iv")

    def test_faction_omega_marked_in_text_is_split_out(self):
        # Faction-page Omega: Ω is in the TEXT ("Ω: ..."), not the name. Yin
        # Spinner and its Ω share a base name and must not collapse to one id.
        self.assertIn("Yin Spinner", self.by_name)
        self.assertIn("Yin Spinner Ω", self.by_name)
        self.assertNotEqual(
            self.by_name["Yin Spinner"]["id"], self.by_name["Yin Spinner Ω"]["id"]
        )

    # --- unit upgrades (the calculator payload) ----------------------------

    def test_unit_upgrade_stats_match_the_cards(self):
        carrier = self.by_name["Carrier II"]["unit_upgrade"]
        self.assertEqual(carrier["unit"], "carrier")
        self.assertEqual(
            {k: carrier[k] for k in ("cost", "combat", "move", "capacity")},
            {"cost": 3, "combat": 9, "move": 2, "capacity": 6},
        )
        # Fighter II has no cost/capacity columns; stats read by label, not index.
        fighter = self.by_name["Fighter II"]["unit_upgrade"]
        self.assertEqual(fighter["combat"], 8)
        self.assertEqual(fighter["move"], 2)

    def test_unit_upgrade_fields_mirror_units_py_keys(self):
        # The payload is meant to be a drop-in override for the combat model, so
        # its stat keys must be a subset of what UnitStats exposes.
        from core.service.combat.units import UnitStats  # local: keep import cheap

        allowed = set(UnitStats.__dataclass_fields__) | {"unit", "abilities", "capacity", "move"}
        for t in self.techs:
            if t["unit_upgrade"]:
                self.assertTrue(
                    set(t["unit_upgrade"]).issubset(allowed),
                    f"{t['name']}: {set(t['unit_upgrade']) - allowed}",
                )

    def test_stat_only_and_ability_only_upgrades_both_survive(self):
        # PDS II has no stat table (ability text only); Hel Titan II has a single
        # stat column. Both were dropped by earlier over-strict parsing.
        self.assertIn("PDS II", self.by_name)
        self.assertIn("Hel Titan II", self.by_name)

    # --- cross-cutting flags -----------------------------------------------

    def test_starting_for_is_populated(self):
        plasma = self.by_name["Plasma Scoring"]
        self.assertIn("The Embers of Muaat", plasma["starting_for"])

    def test_exhaustible_flag_is_set(self):
        self.assertTrue(self.by_name["Bio-Stims"]["exhaustible"])
        self.assertFalse(self.by_name["Neural Motivator"]["exhaustible"])

    # --- discordant stars ---------------------------------------------------

    def test_discordant_stars_techs_are_present_and_tagged(self):
        ds = [t for t in self.techs if t["set"] == "discordant_stars"]
        self.assertGreater(len(ds), 40)
        self.assertIn("Rift Engines", self.by_name)


class TechIngestUnitTest(SimpleTestCase):
    """Small, targeted tests for the parsing helpers."""

    def test_omega_level_reads_name_or_text(self):
        self.assertEqual(T.omega_level("Magen Defense Grid"), 0)
        self.assertEqual(T.omega_level("Magen Defense Grid Ω"), 1)
        self.assertEqual(T.omega_level("Magen Defense Grid ΩΩ"), 2)
        self.assertEqual(T.omega_level("Yin Spinner", "Ω: After you produce..."), 1)

    def test_make_id_keeps_omega_revisions_apart(self):
        self.assertNotEqual(T.make_id("X-89 Ω"), T.make_id("X-89"))
        self.assertEqual(T.make_id("Yin Spinner", "Ω: text"), "yin_spinner_omega")

    def test_prereqs_ignore_non_colour_template_args(self):
        # {{Tech|biotic}} counts; {{Tech|biotic|w=32px}} also names biotic.
        self.assertEqual(T.parse_prereqs("{{Tech|biotic}} {{Tech|warfare}}"),
                         ["biotic", "warfare"])
        self.assertEqual(T.parse_prereqs("{{Edition|Base Game}}"), [])

    def test_edition_maps_to_app_set_vocabulary(self):
        self.assertEqual(T.edition_to_set("Base Game"), "base")
        self.assertEqual(T.edition_to_set("Prophecy of Kings"), "pok")
        self.assertEqual(T.edition_to_set("Codex III"), "keleres")

    def test_unknown_edition_fails_loudly(self):
        with self.assertRaises(T.IngestError):
            T.edition_to_set("Some Future Expansion")

    def test_excluded_edition_never_resolves(self):
        with self.assertRaises(T.IngestError):
            T.edition_to_set("Thunder's Edge")
