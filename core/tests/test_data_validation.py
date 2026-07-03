"""Tests for board data correctness and validation (Milestone 4).

Oracle Rex's board data (``core/util/default_data/``) must match the canonical
Milty Draft export (``core/data/source/``) once naming-convention differences
are applied. These tests assert the live data is clean, that each validator
actually catches the class of error it is responsible for, and that the data
round-trips through the DB and the TTS-string parser correctly.
"""

import copy
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.data import validators
from core.models import Faction, Planet, System
from core.service.tts_string_ingest import HOME_SYSTEM_IDS, build_game_from_string
from core.util.utils import reset_database


class DataIsCleanTests(TestCase):
    """The shipped default data passes every validator."""

    def test_all_validations_pass(self):
        results = validators.run_all_validations()
        problems = {name: issues for name, issues in results.items() if issues}
        self.assertEqual(problems, {}, f"Unexpected data validation issues: {problems}")

    def test_management_command_succeeds(self):
        out = StringIO()
        call_command("validate_data", stdout=out, stderr=StringIO())
        self.assertIn("All data validations passed.", out.getvalue())

    def test_management_command_fails_nonzero_on_bad_data(self):
        bad_systems = copy.deepcopy(validators.DEFAULT_SYSTEMS)
        bad_systems[0]["wormhole"] = "not-a-wormhole"
        with patch.object(validators, "DEFAULT_SYSTEMS", bad_systems):
            with self.assertRaises(SystemExit) as cm:
                call_command("validate_data", stdout=StringIO(), stderr=StringIO())
            self.assertEqual(cm.exception.code, 1)


class InternalConsistencyValidatorTests(TestCase):
    """validate_internal_consistency catches each defect class."""

    def test_clean(self):
        self.assertEqual(validators.validate_internal_consistency(), [])

    def test_catches_duplicate_tile_id(self):
        systems = copy.deepcopy(validators.DEFAULT_SYSTEMS)
        systems.append(copy.deepcopy(systems[0]))  # duplicate tile_id
        with patch.object(validators, "DEFAULT_SYSTEMS", systems):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("Duplicate system tile_id" in i for i in issues), issues)

    def test_catches_invalid_wormhole(self):
        systems = copy.deepcopy(validators.DEFAULT_SYSTEMS)
        systems[0]["wormhole"] = "bogus"
        with patch.object(validators, "DEFAULT_SYSTEMS", systems):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("invalid wormhole" in i for i in issues), issues)

    def test_catches_system_referencing_missing_planet(self):
        systems = copy.deepcopy(validators.DEFAULT_SYSTEMS)
        systems[0]["planets"] = ["Nonexistent Planet"]
        with patch.object(validators, "DEFAULT_SYSTEMS", systems):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("nonexistent planet" in i for i in issues), issues)

    def test_catches_orphan_planet(self):
        planets = copy.deepcopy(validators.DEFAULT_PLANETS)
        planets.append({"name": "Floating Rock", "resources": 1, "influence": 1,
                        "trait": "none", "tech_specialty": "none"})
        with patch.object(validators, "DEFAULT_PLANETS", planets):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("orphan" in i for i in issues), issues)

    def test_catches_invalid_trait(self):
        planets = copy.deepcopy(validators.DEFAULT_PLANETS)
        planets[0]["trait"] = "swampy"
        with patch.object(validators, "DEFAULT_PLANETS", planets):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("invalid trait" in i for i in issues), issues)

    def test_catches_negative_resources(self):
        planets = copy.deepcopy(validators.DEFAULT_PLANETS)
        planets[0]["resources"] = -1
        with patch.object(validators, "DEFAULT_PLANETS", planets):
            issues = validators.validate_internal_consistency()
        self.assertTrue(any("invalid resources" in i for i in issues), issues)


class MiltySourceParityValidatorTests(TestCase):
    """validate_against_milty_source detects drift from the canonical export."""

    def test_clean(self):
        self.assertEqual(validators.validate_against_milty_source(), [])

    def test_catches_resource_mismatch(self):
        planets = copy.deepcopy(validators.DEFAULT_PLANETS)
        next(p for p in planets if p["name"] == "Jord")["resources"] = 99
        with patch.object(validators, "DEFAULT_PLANETS", planets):
            issues = validators.validate_against_milty_source()
        self.assertTrue(any("Jord" in i and "resources" in i for i in issues), issues)

    def test_catches_legendary_mismatch(self):
        planets = copy.deepcopy(validators.DEFAULT_PLANETS)
        next(p for p in planets if p["name"] == "Mallice")["legendary"] = False
        with patch.object(validators, "DEFAULT_PLANETS", planets):
            issues = validators.validate_against_milty_source()
        self.assertTrue(any("Mallice" in i and "legendary" in i for i in issues), issues)


class FactionParityValidatorTests(TestCase):
    def test_clean(self):
        self.assertEqual(validators.validate_factions_against_milty_source(), [])

    def test_catches_unknown_faction_id(self):
        factions = copy.deepcopy(validators.DEFAULT_FACTIONS)
        factions[0]["name"] = "muatt"  # the old typo, not a Milty id
        with patch.object(validators, "DEFAULT_FACTIONS", factions):
            issues = validators.validate_factions_against_milty_source()
        self.assertTrue(any("muatt" in i for i in issues), issues)


class TileImageValidatorTests(TestCase):
    def test_clean(self):
        self.assertEqual(validators.validate_tile_images(), [])

    def test_catches_missing_image(self):
        # Pretend the known-gap tiles are not exempt; they then surface as missing.
        with patch.object(validators, "KNOWN_MISSING_IMAGE_TILE_IDS", set()):
            issues = validators.validate_tile_images()
        self.assertTrue(any("4224" in i for i in issues), issues)


class TtsParserConfigValidatorTests(TestCase):
    def test_clean(self):
        self.assertEqual(validators.validate_tts_parser_config(), [])

    def test_edyn_home_is_accepted(self):
        # Regression: the old hardcoded range omitted Edyn (4236).
        edyn_tile = next(s["tile_id"] for s in validators.DEFAULT_SYSTEMS
                         if s["name"] == "Edyn System")
        self.assertIn(edyn_tile, HOME_SYSTEM_IDS)

    def test_anomaly_backtiles_not_home_systems(self):
        # Regression: the old range wrongly accepted anomaly tiles 4224/4225.
        self.assertNotIn(4224, HOME_SYSTEM_IDS)
        self.assertNotIn(4225, HOME_SYSTEM_IDS)


class LrrCorpusValidatorTests(TestCase):
    """The vendored Living Rules Reference corpus is well-formed."""

    def _corpus(self):
        """A minimal valid corpus fixture the negative tests can mutate."""
        return {
            "provenance": {"source": "LRR", "version": "2.0", "source_url": "http://x"},
            "chunks": [
                {"rule_id": "58", "topic": "Movement", "parent_topic": None,
                 "kind": "topic_intro", "text": "A player can move their ships.",
                 "related": ["Adjacency"]},
                {"rule_id": "58.3", "topic": "Movement", "parent_topic": "58",
                 "kind": "rule", "text": "A ship's move value...", "related": []},
                {"rule_id": "6", "topic": "Adjacency", "parent_topic": None,
                 "kind": "topic_intro", "text": "Two systems are adjacent...",
                 "related": []},
            ],
        }

    def _run_with(self, corpus):
        with patch.object(validators.json, "load", return_value=corpus):
            return validators.validate_lrr_corpus()

    def test_real_corpus_clean(self):
        self.assertEqual(validators.validate_lrr_corpus(), [])

    def test_fixture_clean(self):
        self.assertEqual(self._run_with(self._corpus()), [])

    def test_catches_empty_text(self):
        corpus = self._corpus()
        corpus["chunks"][1]["text"] = "   "
        self.assertTrue(any("empty text" in i for i in self._run_with(corpus)))

    def test_catches_duplicate_rule_id(self):
        corpus = self._corpus()
        corpus["chunks"][1]["rule_id"] = "58"  # collide with the topic intro
        self.assertTrue(any("duplicate" in i.lower() for i in self._run_with(corpus)))

    def test_catches_invalid_rule_id(self):
        corpus = self._corpus()
        corpus["chunks"][1]["rule_id"] = "58.a"
        self.assertTrue(any("invalid rule_id" in i for i in self._run_with(corpus)))

    def test_catches_unresolved_related(self):
        corpus = self._corpus()
        corpus["chunks"][0]["related"] = ["No Such Topic"]
        self.assertTrue(any("unresolved related" in i for i in self._run_with(corpus)))

    def test_catches_orphan_subrule(self):
        corpus = self._corpus()
        corpus["chunks"][1]["parent_topic"] = "999"
        self.assertTrue(any("unknown parent_topic" in i for i in self._run_with(corpus)))

    def test_related_resolves_ignoring_qualifier(self):
        # "Combat" must resolve to the topic "Combat (Attribute)".
        corpus = self._corpus()
        corpus["chunks"].append(
            {"rule_id": "18", "topic": "Combat (Attribute)", "parent_topic": None,
             "kind": "topic_intro", "text": "Combat is an attribute.", "related": []})
        corpus["chunks"][0]["related"] = ["Combat"]
        self.assertEqual(self._run_with(corpus), [])

    def test_included_in_run_all(self):
        self.assertIn("lrr_corpus", validators.run_all_validations())


class DataRoundTripTests(TestCase):
    """The data is correct once loaded into the DB by reset_database()."""

    def setUp(self):
        reset_database()

    def test_legendary_planets_loaded(self):
        legendary = set(Planet.objects.filter(legendary=True).values_list("name", flat=True))
        self.assertEqual(
            legendary,
            {"Primor", "Hope's End", "Mallice", "Silence", "Echo", "Tarrock", "Prism", "Domna"},
        )

    def test_faction_ids_match_milty(self):
        self.assertTrue(Faction.objects.filter(name="muaat").exists())
        self.assertTrue(Faction.objects.filter(name="norr").exists())
        self.assertTrue(Faction.objects.filter(name="freesystems").exists())
        self.assertFalse(Faction.objects.filter(name="muatt").exists())

    def test_anomaly_values_valid(self):
        self.assertEqual(System.objects.get(tile_id=81).anomaly, "muaat-supernova")
        self.assertEqual(System.objects.get(tile_id=4275).anomaly, "gravity-rift")
        self.assertEqual(System.objects.filter(anomaly="nebula").count(), 5)

    def test_planet_to_json_includes_legendary(self):
        self.assertTrue(Planet.objects.get(name="Mallice").to_json()["legendary"])
        self.assertFalse(Planet.objects.get(name="Jord").to_json()["legendary"])

    def test_build_game_with_edyn_home(self):
        # A draft string with Edyn (4236) as a home system must now validate;
        # build a 36-id string with Edyn in an outer-ring home slot.
        ids = ["78", "40", "42", "67", "28", "38",        # ring 1 (6)
               "76", "43", "21", "44", "77", "63",        # ring 2 first half
               "50", "64", "74", "48", "49", "39",        # ring 2 second half
               "4236", "71", "35", "16", "27", "36",      # outer: 4236 (Edyn) is a home slot
               "55", "31", "20", "58", "69", "45",
               "4", "23", "22", "57", "34", "25"]
        game = build_game_from_string(" ".join(ids), "Test")
        factions = {p.faction.name for p in game.players.all()}
        self.assertIn("edyn", factions)

    def test_board_render_tiles_have_images(self):
        """Every system placed on a built board has a board graphic (render parity)."""
        id_string = ("78 40 42 67 28 38 76 43 21 44 77 63 50 64 74 48 49 39 "
                     "1 71 35 16 27 36 55 31 20 58 69 45 4 23 22 57 34 25")
        game = build_game_from_string(id_string, "Test")
        for tile in game.board.all():
            if tile.system is None:
                continue
            tid = tile.system.tile_id
            if int(tid) in validators.KNOWN_MISSING_IMAGE_TILE_IDS:
                continue
            self.assertTrue(
                (validators.SYSTEM_IMAGE_DIR / f"ST_{tid}.png").exists(),
                f"Missing board image for placed tile {tid}",
            )
