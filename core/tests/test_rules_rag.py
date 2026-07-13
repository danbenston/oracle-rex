"""Phase 2 (pipeline integration) tests for grounded Rules Q&A.

Mocked end to end — no provider call, no index required. Covers the pieces that
turn recall into grounded answers: the rules_chat_v3 prompt, retrieval wiring,
post-hoc citation validation (the deterministic anti-hallucination check),
grounded derivation, graceful degradation, and the passages threaded into the
job payload.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from core import jobs
from core.service.ai import config, service
from core.service.ai.prompts import rules_chat
from core.service.ai.schemas import RuleCitation, RulesAnswer

PASSAGES = [
    {"rule_id": "78.7", "topic": "Space Combat",
     "text": "STEP 5-RETREAT: If a player announced a retreat and there is still an "
             "eligible system, they must retreat.", "score": 9.0},
    {"rule_id": "78.4", "topic": "Space Combat",
     "text": "STEP 2-ANNOUNCE RETREATS: Each player may announce a retreat, "
             "beginning with the defender.", "score": 8.0},
]


class _StructuredOK:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class _FakeChat:
    """Returns a preset structured RulesAnswer."""
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return _StructuredOK(self._result)

    def invoke(self, messages):
        class _R:
            content = ""
        return _R()


class _FakeChatFallback:
    """Structured output unsupported -> service falls back to plain text."""
    def __init__(self, plain):
        self._plain = plain

    def with_structured_output(self, schema):
        raise RuntimeError("structured output not supported")

    def invoke(self, messages):
        plain = self._plain

        class _R:
            content = plain
        return _R()


def _run(model_answer, passages, rag=True):
    with patch.object(config, "RULES_RAG_ENABLED", rag), \
            patch.object(service, "get_chat", return_value=_FakeChat(model_answer)), \
            patch.object(service, "_retrieve_rules_passages", return_value=passages):
        return service.get_rules_result("can I retreat with no ships?", "key", "model")


class RulesPromptTests(SimpleTestCase):
    def test_grounded_prompt_includes_reference_block(self):
        msgs = rules_chat.build_messages("Can I retreat?", PASSAGES)
        text = "\n".join(m.content for m in msgs)
        self.assertIn("RULES REFERENCE", text)
        self.assertIn("78.7", text)
        self.assertIn("grounded", msgs[0].content)

    def test_recall_prompt_has_no_reference_block(self):
        msgs = rules_chat.build_messages("Can I retreat?", None)
        text = "\n".join(m.content for m in msgs)
        self.assertNotIn("RULES REFERENCE", text)
        # The question is passed through as the final human turn.
        self.assertEqual(msgs[-1].content, "Can I retreat?")


class RulesRagServiceTests(SimpleTestCase):
    def test_grounded_answer_keeps_valid_citation(self):
        answer = RulesAnswer(
            answer="No — a retreat needs surviving ships and an eligible system.",
            citations=[RuleCitation(rule_id="78.7", relevance="retreat requires an eligible system")],
            grounded=True,
        )
        result = _run(answer, PASSAGES)
        self.assertEqual([c.rule_id for c in result.answer.citations], ["78.7"])
        self.assertTrue(result.answer.grounded)
        self.assertEqual(result.passages, PASSAGES)

    def test_hallucinated_citation_is_dropped_with_warning(self):
        answer = RulesAnswer(
            answer="You cannot retreat.",
            citations=[
                RuleCitation(rule_id="99.99", relevance="invented"),
                RuleCitation(rule_id="78.4", relevance="announce retreats"),
            ],
            grounded=True,
        )
        with self.assertLogs("core.service.ai.service", level="WARNING") as logs:
            result = _run(answer, PASSAGES)
        self.assertEqual([c.rule_id for c in result.answer.citations], ["78.4"])
        self.assertTrue(result.answer.grounded)  # a valid citation survives
        self.assertTrue(any("99.99" in m for m in logs.output))

    def test_out_of_corpus_answer_is_ungrounded(self):
        # Model correctly declines to cite when passages don't cover the question.
        answer = RulesAnswer(
            answer="That's Discordant Stars content, which isn't in the LRR; from "
                   "general knowledge...",
            citations=[],
            grounded=False,
        )
        result = _run(answer, PASSAGES)  # passages retrieved but unused
        self.assertEqual(result.answer.citations, [])
        self.assertFalse(result.answer.grounded)

    def test_grounded_is_false_when_only_invalid_citations(self):
        answer = RulesAnswer(
            answer="...",
            citations=[RuleCitation(rule_id="12.34", relevance="not retrieved")],
            grounded=True,  # model claims grounded; validation overrides
        )
        result = _run(answer, PASSAGES)
        self.assertEqual(result.answer.citations, [])
        self.assertFalse(result.answer.grounded)

    def test_inline_rule_refs_are_harvested_into_citations(self):
        # Small model wrote rule numbers in prose and left the citations field
        # empty; the retrieved ones must be recovered so the answer isn't
        # falsely marked ungrounded (the drop-off screenshot regression).
        answer = RulesAnswer(
            answer="Ships must end movement in the active system [58.4], and the "
                   "transport rules [78.4] do not allow intermediate drop-offs.",
            citations=[],
            grounded=False,
        )
        passages = [
            {"rule_id": "58.4", "topic": "Movement", "text": "STEP 1-MOVE SHIPS ..."},
            {"rule_id": "78.4", "topic": "Space Combat", "text": "..."},
        ]
        result = _run(answer, passages)
        self.assertEqual({c.rule_id for c in result.answer.citations}, {"58.4", "78.4"})
        self.assertTrue(result.answer.grounded)

    def test_only_dotted_retrieved_ids_are_harvested(self):
        # A bare topic number ("95") and an incidental number ("2 tiles") must
        # not be harvested — only dotted rule_ids that were actually retrieved.
        answer = RulesAnswer(
            answer="Move up to 2 tiles; see rule 95 and 58.4 for the details.",
            citations=[],
            grounded=False,
        )
        passages = [{"rule_id": "58.4", "topic": "Movement", "text": "x"}]
        result = _run(answer, passages)
        self.assertEqual({c.rule_id for c in result.answer.citations}, {"58.4"})

    def test_fallback_answer_is_ungrounded(self):
        with patch.object(config, "RULES_RAG_ENABLED", True), \
                patch.object(service, "_retrieve_rules_passages", return_value=PASSAGES), \
                patch.object(service, "get_chat",
                             return_value=_FakeChatFallback("You cannot retreat with no ships.")):
            result = service.get_rules_result("q?", "key", "model")
        self.assertFalse(result.answer.grounded)
        self.assertEqual(result.answer.citations, [])
        self.assertIn("retreat", result.answer.answer.lower())

    def test_flag_off_skips_retrieval_and_drops_citations(self):
        # RULES_RAG_ENABLED=0: no retrieval, pre-RAG path, any model citation is
        # dropped (nothing was retrieved to validate against).
        answer = RulesAnswer(
            answer="Recalled answer.",
            citations=[RuleCitation(rule_id="78.7", relevance="x")],
            grounded=True,
        )
        with patch.object(config, "RULES_RAG_ENABLED", False), \
                patch.object(service, "get_chat", return_value=_FakeChat(answer)):
            result = service.get_rules_result("q?", "key", "model")
        self.assertEqual(result.passages, [])
        self.assertEqual(result.answer.citations, [])
        self.assertFalse(result.answer.grounded)

    def test_missing_index_degrades_to_ungrounded(self):
        from core.service.rules_index import RulesIndexError

        answer = RulesAnswer(answer="Recalled answer.")
        with patch.object(config, "RULES_RAG_ENABLED", True), \
                patch("core.service.rules_index.retrieve",
                      side_effect=RulesIndexError("index not built")), \
                patch.object(service, "get_chat", return_value=_FakeChat(answer)):
            result = service.get_rules_result("q?", "key", "model")
        self.assertEqual(result.passages, [])
        self.assertFalse(result.answer.grounded)

    def test_retrieve_rules_passages_uses_real_index(self):
        # End-to-end retrieval (real corpus + real FTS5), just no provider call.
        import shutil
        import tempfile
        from pathlib import Path

        from core.service import rules_index

        tmp = tempfile.mkdtemp(prefix="rag-int-")
        try:
            idx = Path(tmp) / "lrr.sqlite3"
            rules_index.build_index(rules_index.SOURCE_PATH, idx)
            with patch.object(config, "RULES_RAG_ENABLED", True), \
                    patch.object(rules_index, "INDEX_PATH", idx):
                passages = service._retrieve_rules_passages("can I retreat with no ships left?")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertTrue(passages, "expected real retrieval to return passages")
        self.assertTrue(any(p["rule_id"].startswith("78") for p in passages))
        self.assertTrue(all({"rule_id", "topic", "text", "score"} <= set(p) for p in passages))

    def test_get_rules_response_still_returns_answer(self):
        # Backward-compatible wrapper (used by the promptfoo provider, demo, ...).
        answer = RulesAnswer(answer="ok", citations=[RuleCitation(rule_id="78.7")], grounded=True)
        with patch.object(config, "RULES_RAG_ENABLED", True), \
                patch.object(service, "_retrieve_rules_passages", return_value=PASSAGES), \
                patch.object(service, "get_chat", return_value=_FakeChat(answer)):
            out = service.get_rules_response("q?", "key", "model")
        self.assertIsInstance(out, RulesAnswer)
        self.assertEqual([c.rule_id for c in out.citations], ["78.7"])


class RulesJobPayloadTests(SimpleTestCase):
    def test_run_rules_threads_passages_into_payload(self):
        answer = RulesAnswer(
            answer="No.",
            citations=[RuleCitation(rule_id="78.7", relevance="retreat needs a system")],
            grounded=True,
        )
        result = service.RulesResult(answer=answer, passages=PASSAGES)
        with patch.object(jobs.service, "get_rules_result", return_value=result):
            payload = jobs._run_rules({"question": "Can I retreat?"}, "key", "model")
        self.assertEqual(payload["passages"], PASSAGES)
        self.assertIn("structured", payload)
        self.assertTrue(payload["structured"]["grounded"])
        self.assertIn("LRR 78.7", payload["answer"])  # citation rendered in plain text
