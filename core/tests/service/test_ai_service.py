"""Offline tests for the AI service layer (no network / API calls)."""

from unittest.mock import patch

from django.test import TestCase
from langchain_core.messages import AIMessage

from ...service.ai import config
from ...service.ai.clients import get_chat
from ...service.ai.errors import (
    InvalidAPIKeyError,
    MalformedResponseError,
    MissingAPIKeyError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    QuotaExceededError,
    classify_provider_error,
)
from ...service.ai.schemas import RulesAnswer, StrategicPlan, TacticalMove
from ...service.ai import service


class TestConfig(TestCase):
    def test_provider_for_known_models(self):
        self.assertEqual(config.provider_for_model("gpt-5.6-terra"), config.OPENAI)
        self.assertEqual(config.provider_for_model("grok-4.3"), config.XAI)
        self.assertEqual(config.provider_for_model("claude-opus-4-8"), config.ANTHROPIC)

    def test_unknown_model_resolves_to_fallback(self):
        # The legacy frontend still sends "gpt-4"; retired models resolve too.
        self.assertEqual(config.resolve_model("gpt-4"), config.FALLBACK_MODEL)
        self.assertEqual(config.resolve_model("gpt-4.1-nano"), config.FALLBACK_MODEL)
        self.assertEqual(config.resolve_model(None), config.FALLBACK_MODEL)
        self.assertEqual(config.resolve_model("gpt-5.6-terra"), "gpt-5.6-terra")


class TestClientFactory(TestCase):
    def test_missing_api_key_raises(self):
        with self.assertRaises(MissingAPIKeyError):
            get_chat("gpt-5.6-terra", "", 4000)

    def test_openai_forwards_reasoning_effort(self):
        chat = get_chat("gpt-5.6-terra", "fake-key", 4000, reasoning_effort="medium")
        self.assertEqual(chat.reasoning_effort, "medium")

    def test_xai_ignores_reasoning_effort(self):
        # Grok reasons on its own; passing effort must not break client build.
        chat = get_chat("grok-4.3", "fake-key", 4000, reasoning_effort="medium")
        self.assertEqual(chat.model_name, "grok-4.3")

    def test_anthropic_requested_without_package_is_clear_error(self):
        # Only meaningful where langchain_anthropic is absent; there it should be
        # a clean ProviderError, not an unhandled ImportError.
        try:
            import langchain_anthropic  # noqa: F401
        except ImportError:
            with self.assertRaises(ProviderError):
                get_chat("claude-opus-4-8", "fake-key", 500)

    def test_anthropic_forwards_effort_when_model_supports_it(self):
        chat = get_chat("claude-sonnet-5", "fake-key", 4000, reasoning_effort="medium")
        self.assertEqual(chat.effort, "medium")

    def test_anthropic_omits_effort_when_model_rejects_it(self):
        # Haiku 4.5 predates the effort parameter and the API 400s on it, so the
        # client must drop it rather than pass the per-feature value through.
        chat = get_chat("claude-haiku-4-5", "fake-key", 4000, reasoning_effort="medium")
        self.assertIsNone(chat.effort)

    def test_effort_support_matches_configured_anthropic_models(self):
        # Guards the swap this catalog is built for: every effort-capable id must
        # be a model we actually serve, so a rename can't leave a stale entry
        # silently un-gated.
        self.assertTrue(
            set(config.ANTHROPIC_EFFORT_MODELS).issubset(set(config.ANTHROPIC_MODELS))
        )


class TestErrorClassification(TestCase):
    def test_timeout(self):
        self.assertIsInstance(
            classify_provider_error(Exception("Request timed out")),
            ProviderTimeoutError,
        )

    def test_rate_limit(self):
        self.assertIsInstance(
            classify_provider_error(Exception("Error code: 429 rate limit")),
            ProviderRateLimitError,
        )

    def test_insufficient_quota_is_not_treated_as_rate_limit(self):
        # OpenAI returns insufficient_quota as a 429 with the same exception
        # class as real rate limiting; it must classify as a quota/billing error.
        msg = (
            "Error code: 429 - You exceeded your current quota, please check "
            "your plan and billing details. (insufficient_quota)"
        )
        self.assertIsInstance(classify_provider_error(Exception(msg)), QuotaExceededError)

    def test_auth(self):
        self.assertIsInstance(
            classify_provider_error(Exception("Incorrect API key provided")),
            InvalidAPIKeyError,
        )

    def test_generic(self):
        self.assertIsInstance(
            classify_provider_error(Exception("something weird happened")),
            ProviderError,
        )


class TestSchemas(TestCase):
    def test_rules_answer_display_text(self):
        ans = RulesAnswer(
            answer="You may not.",
            rule_basis=["Movement rules"],
            caveats=["Unless you have a relevant tech"],
        )
        text = ans.to_display_text()
        self.assertIn("You may not.", text)
        self.assertIn("Rule basis:", text)
        self.assertIn("- Movement rules", text)

    def test_strategic_plan_fallback_from_text(self):
        plan = StrategicPlan.fallback_from_text("Expand fast.")
        self.assertEqual(plan.summary, "Expand fast.")
        self.assertIn("Expand fast.", plan.to_display_text())

    def test_tactical_move_fallback_from_text(self):
        move = TacticalMove.fallback_from_text("Move the carrier.")
        self.assertEqual(move.recommended_move, "Move the carrier.")


class _FakeStructured:
    """Stand-in for chat.with_structured_output(schema).invoke()."""

    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeChat:
    def __init__(self, structured_result=None, plain_content=None, invoke_exc=None):
        self._structured_result = structured_result
        self._plain_content = plain_content
        self._invoke_exc = invoke_exc

    def with_structured_output(self, schema):
        return _FakeStructured(self._structured_result)

    def invoke(self, messages):
        if self._invoke_exc:
            raise self._invoke_exc
        # A real AIMessage, not a stand-in with a bare `.content` string. The
        # provider's own message type is what the service reads, and its
        # content/text handling is the whole subtlety here: a hand-rolled fake
        # that is always a plain string can't reproduce the block-content shape
        # that thinking models actually return.
        return AIMessage(content=self._plain_content)


class TestServiceFlow(TestCase):
    def test_structured_success(self):
        expected = RulesAnswer(answer="Yes.")
        chat = _FakeChat(structured_result=expected)
        with patch.object(service, "get_chat", return_value=chat):
            result = service.get_rules_response("Can I do X?", "key", "gpt-5.6-terra")
        self.assertIsInstance(result, RulesAnswer)
        self.assertEqual(result.answer, "Yes.")

    def test_structured_falls_back_to_plain_text(self):
        # Structured output errors with a non-recoverable-looking parse error;
        # service should fall back to a plain-text call and wrap it.
        chat = _FakeChat(
            structured_result=ValueError("schema not supported"),
            plain_content="Plain answer.",
        )
        with patch.object(service, "get_chat", return_value=chat):
            result = service.get_rules_response("Q?", "key", "grok-4.3")
        self.assertEqual(result.answer, "Plain answer.")

    def test_auth_error_is_not_swallowed_by_fallback(self):
        chat = _FakeChat(structured_result=Exception("Incorrect API key provided"))
        with patch.object(service, "get_chat", return_value=chat):
            with self.assertRaises(InvalidAPIKeyError):
                service.get_rules_response("Q?", "bad-key", "gpt-5.6-terra")

    def test_empty_plain_content_is_malformed(self):
        chat = _FakeChat(plain_content="   ")
        with patch.object(service, "get_chat", return_value=chat):
            with self.assertRaises(MalformedResponseError):
                service.get_tac_calc_response(
                    {"friendly": {}}, api_key="key", model="gpt-5.6-terra"
                )

    def test_plain_content_as_blocks_is_read_not_rejected(self):
        # A thinking model returns content as a list of typed blocks rather than
        # a string. That is a valid answer, not a malformed one — reading
        # `.content` and demanding a str rejected real Gemini answers on tac_calc.
        chat = _FakeChat(
            plain_content=[
                {
                    "type": "text",
                    "text": "With a 66% win probability, hold position.",
                    "extras": {"signature": "EjQKMgERTTIPlt5UgPLP"},
                }
            ]
        )
        with patch.object(service, "get_chat", return_value=chat):
            result = service.get_tac_calc_response(
                {"friendly": {}}, api_key="key", model="gemini-3.1-flash-lite"
            )
        self.assertEqual(result, "With a 66% win probability, hold position.")

    def test_thinking_only_blocks_with_no_text_is_malformed(self):
        # The genuine empty-output case this guard exists for: the model spent
        # its budget thinking and produced no answer. Must still raise.
        chat = _FakeChat(plain_content=[{"type": "thinking", "thinking": "hmm..."}])
        with patch.object(service, "get_chat", return_value=chat):
            with self.assertRaises(MalformedResponseError):
                service.get_tac_calc_response(
                    {"friendly": {}}, api_key="key", model="gemini-3.1-flash-lite"
                )

    def test_input_validation(self):
        from ...service.ai.errors import InputValidationError

        with self.assertRaises(InputValidationError):
            service.get_rules_response("", "key", "gpt-5.6-terra")
