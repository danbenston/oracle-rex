"""Tier A retrieval eval for grounded Rules Q&A — deterministic, free, in CI.

This is the regression gate for the retrieval engine (chunking + FTS5 index +
BM25 weights + jargon aliases). It runs the golden set
(``core/data/eval/rules_golden.json``) through :func:`retrieve` and asserts
recall@k / MRR stay at or above committed floors. A chunking or alias change that
drops recall fails here — no provider call, no tokens, so it can run on every
change (unlike the Tier B promptfoo answer evals).

Baselines recorded 2026-07-03 on LRR 2.0 (32 cases):
  recall@3 = 0.844 · recall@5 = 0.969 · recall@8 = 1.000 · recall@10 = 1.000
  MRR@10  = 0.700
Floors below sit under those with margin so a later golden addition doesn't
immediately break CI, while a real retrieval regression still trips them.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from core.service.rules_index import SOURCE_PATH, build_index, retrieve

_DATA_DIR = SOURCE_PATH.parents[2]  # .../core/data
GOLDEN_PATH = _DATA_DIR / "eval" / "rules_golden.json"

# Regression floors (baseline in the module docstring; deterministic BM25, so
# these are stable, not flaky).
RECALL_AT_5_FLOOR = 0.90
RECALL_AT_8_FLOOR = 0.97
RECALL_AT_10_FLOOR = 0.97
MRR_FLOOR = 0.62


class RulesRetrievalEvalTests(SimpleTestCase):
    """Retrieval quality gate over the golden set (no app DB needed)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.mkdtemp(prefix="rules-eval-")
        cls.index_path = Path(cls._tmp) / "lrr_fts.sqlite3"
        build_index(SOURCE_PATH, cls.index_path)

        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        cls.cases = golden["cases"]
        corpus = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
        cls.corpus_ids = {c["rule_id"] for c in corpus["chunks"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)
        super().tearDownClass()

    # --- helpers -----------------------------------------------------------

    def _first_hit_rank(self, case, k):
        ids = [r.rule_id for r in retrieve(case["question"], k=k, index_path=self.index_path)]
        for i, rid in enumerate(ids, 1):
            if rid in case["expected_rule_ids"]:
                return i
        return None

    def _recall_at(self, k):
        hits = sum(1 for c in self.cases if self._first_hit_rank(c, k) is not None)
        return hits / len(self.cases)

    def _mrr(self, kmax=10):
        total = 0.0
        for c in self.cases:
            rank = self._first_hit_rank(c, kmax)
            if rank:
                total += 1.0 / rank
        return total / len(self.cases)

    # --- golden-set integrity ---------------------------------------------

    def test_golden_set_nonempty(self):
        self.assertGreaterEqual(len(self.cases), 25, "golden set should have >= 25 cases")

    def test_every_expected_id_exists_in_corpus(self):
        missing = [
            (c["id"], rid)
            for c in self.cases
            for rid in c["expected_rule_ids"]
            if rid not in self.corpus_ids
        ]
        self.assertEqual(missing, [], f"golden expects rule_ids absent from corpus: {missing}")

    def test_case_ids_unique(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)), "duplicate golden case ids")

    # --- retrieval quality floors -----------------------------------------

    def test_recall_at_5_floor(self):
        r = self._recall_at(5)
        self.assertGreaterEqual(r, RECALL_AT_5_FLOOR, f"recall@5 regressed to {r:.3f}")

    def test_recall_at_8_floor(self):
        r = self._recall_at(8)
        self.assertGreaterEqual(r, RECALL_AT_8_FLOOR, f"recall@8 regressed to {r:.3f}")

    def test_recall_at_10_floor(self):
        r = self._recall_at(10)
        self.assertGreaterEqual(r, RECALL_AT_10_FLOOR, f"recall@10 regressed to {r:.3f}")

    def test_mrr_floor(self):
        m = self._mrr(10)
        self.assertGreaterEqual(m, MRR_FLOOR, f"MRR@10 regressed to {m:.3f}")

    # --- behavior spot-checks ---------------------------------------------

    def test_retrieval_is_deterministic(self):
        q = "can I retreat with no ships left?"
        first = [r.rule_id for r in retrieve(q, k=8, index_path=self.index_path)]
        second = [r.rule_id for r in retrieve(q, k=8, index_path=self.index_path)]
        self.assertEqual(first, second)

    def test_jargon_alias_maps_pds_to_space_cannon(self):
        # "PDS" never appears literally in most Space Cannon rules; the alias map
        # must still surface them.
        ids = [r.rule_id for r in retrieve("what does PDS do", k=8, index_path=self.index_path)]
        self.assertTrue(
            any(rid.startswith("77") or rid.startswith("63") for rid in ids),
            f"PDS alias failed to surface Space Cannon / PDS rules: {ids}",
        )

    def test_empty_question_returns_no_results(self):
        self.assertEqual(retrieve("   ??? ", k=8, index_path=self.index_path), [])
