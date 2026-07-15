"""Tests for Demo Mode and private live-demo access (Milestone 3).

Demo mode must work with no API key and must never make a provider call, so the
cached responses are validated against the same schemas the live features use,
and the demo endpoints are exercised end-to-end (create -> poll) offline.
"""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from core import demo
from core.models import AIJob
from core.service.ai import config
from core.service.ai.crypto import decrypt_key
from core.service.ai.schemas import RulesAnswer, StrategicPlan, TacticalMove

# Which schema each structured demo response must validate against.
_STRUCTURED_SCHEMA = {
    "rules": RulesAnswer,
    "strategy": StrategicPlan,
    "move": TacticalMove,
}


class TestDemoCatalogData(TestCase):
    def test_every_runnable_scenario_has_a_loadable_response(self):
        keys = demo.runnable_keys()
        self.assertTrue(keys, "expected at least one runnable demo scenario")
        for key in keys:
            result = demo.get_demo_result(key)
            self.assertIsNotNone(result, f"no demo result for {key}")
            self.assertTrue(result["demo"])
            self.assertTrue(result["demo_label"])

    def test_structured_responses_validate_against_their_schema(self):
        for key in demo.runnable_keys():
            feature = demo.feature_for(key)
            schema = _STRUCTURED_SCHEMA.get(feature)
            result = demo.get_demo_result(key)
            if schema is not None:
                # Should not raise — cached structured data stays schema-valid.
                schema.model_validate(result["structured"])
            else:  # tac_calc returns a plain-text block
                self.assertIn("calc_results", result)

    def test_unknown_scenario_returns_none(self):
        self.assertIsNone(demo.get_demo_result("does_not_exist"))

    def test_catalog_covers_each_feature(self):
        catalog = demo.get_catalog()
        scenarios = catalog["scenarios"]
        for feature in ("rules", "strategy", "move", "tac_calc"):
            self.assertIn(feature, scenarios)
        self.assertTrue(catalog["label"])

    def test_get_demo_result_does_not_mutate_cached_payload(self):
        # The loader caches parsed JSON; callers must get a fresh copy each time.
        first = demo.get_demo_result(demo.runnable_keys()[0])
        first["answer"] = "tampered"
        second = demo.get_demo_result(demo.runnable_keys()[0])
        self.assertNotEqual(second.get("answer"), "tampered")


class TestDemoEndpoints(TestCase):
    def test_catalog_endpoint(self):
        resp = self.client.get(reverse("demo_catalog"))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("scenarios", body)
        self.assertIn("strategy", body["scenarios"])

    def test_demo_run_creates_completed_job_with_cached_result(self):
        resp = self.client.post(
            reverse("demo_job_create"),
            data=json.dumps({"scenario_key": "sample_opening_board"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202)
        job_id = resp.json()["job_id"]

        job = AIJob.objects.get(pk=job_id)
        self.assertEqual(job.status, AIJob.Status.COMPLETED)
        self.assertEqual(job.feature_type, AIJob.FeatureType.STRATEGY)
        self.assertEqual(job.model_provider, "demo")
        self.assertTrue(job.result_payload_json["demo"])
        self.assertIn("strategy", job.result_payload_json)

    def test_demo_run_then_poll_returns_result(self):
        create = self.client.post(
            reverse("demo_job_create"),
            data=json.dumps({"scenario_key": "rules_retreat"}),
            content_type="application/json",
        )
        job_id = create.json()["job_id"]

        status = self.client.get(reverse("ai_job_status", args=[job_id]))
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body["status"], AIJob.Status.COMPLETED)
        self.assertTrue(body["is_terminal"])
        self.assertTrue(body["result"]["demo"])
        self.assertIn("answer", body["result"])

    def test_demo_run_unknown_scenario_is_404(self):
        resp = self.client.post(
            reverse("demo_job_create"),
            data=json.dumps({"scenario_key": "nope"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


@override_settings(
    DEMO_LIVE_ENABLED=True,
    DEMO_LIVE_ACCESS_CODE="let-me-in",
    DEMO_LIVE_API_KEY="sk-owner-secret",
    DEMO_LIVE_MODEL="gpt-5.4-nano",
    DEMO_LIVE_MAX_OUTPUT_TOKENS=0,  # 0 => use reasoning-safe per-feature caps
    DEMO_LIVE_DAILY_LIMIT=50,
)
class TestLiveDemoAccess(TestCase):
    def setUp(self):
        cache.clear()  # reset the per-day live-demo request counter

    def _post_rules(self, body):
        with patch("core.views.enqueue_ai_job"):
            return self.client.post(
                reverse("rules_job_create"),
                data=json.dumps(body),
                content_type="application/json",
            )

    def test_valid_access_code_uses_owner_key_and_cheap_model(self):
        with patch("core.views.enqueue_ai_job") as mock_enqueue:
            resp = self.client.post(
                reverse("rules_job_create"),
                data=json.dumps({"question": "Can I retreat?", "access_code": "let-me-in"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)

        job = AIJob.objects.get(pk=resp.json()["job_id"])
        self.assertEqual(job.model_name, "gpt-5.4-nano")
        # Per-feature reasoning-safe cap carried as an internal payload directive.
        self.assertEqual(
            job.input_payload_json.get("_max_tokens"),
            config.live_demo_max_tokens("rules"),
        )

        # Owner key is encrypted into the enqueue arg, never stored on the row.
        args, _ = mock_enqueue.call_args
        self.assertEqual(decrypt_key(args[1]), "sk-owner-secret")
        self.assertNotIn("sk-owner-secret", json.dumps(job.input_payload_json))

    def test_strategy_live_demo_is_not_starved(self):
        # The heavy reasoning features must keep a generous cap (the #2 fix):
        # well above the lightweight features and far above an empty-output floor.
        with patch("core.views.enqueue_ai_job"):
            resp = self.client.post(
                reverse("strategy_job_create"),
                data=json.dumps({
                    "game_json": {"board": [1]},
                    "player_faction": "sol",
                    "access_code": "let-me-in",
                }),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        job = AIJob.objects.get(pk=resp.json()["job_id"])
        cap = job.input_payload_json.get("_max_tokens")
        self.assertEqual(cap, config.live_demo_max_tokens("strategy"))
        self.assertGreaterEqual(cap, 4000)              # not starved
        self.assertLess(cap, config.STRATEGY_MAX_TOKENS)  # still genuinely caps cost

    @override_settings(DEMO_LIVE_MAX_OUTPUT_TOKENS=1500)
    def test_explicit_env_ceiling_overrides_per_feature_cap(self):
        with patch("core.views.enqueue_ai_job"):
            resp = self._post_rules({"question": "Q?", "access_code": "let-me-in"})
        job = AIJob.objects.get(pk=resp.json()["job_id"])
        self.assertEqual(job.input_payload_json.get("_max_tokens"), 1500)

    def test_wrong_access_code_is_rejected(self):
        resp = self._post_rules({"question": "Q?", "access_code": "wrong"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(AIJob.objects.count(), 0)

    def test_byok_key_still_works_when_no_access_code(self):
        with patch("core.views.enqueue_ai_job") as mock_enqueue:
            resp = self.client.post(
                reverse("rules_job_create"),
                data=json.dumps({"question": "Q?", "api_key": "sk-mine", "model": "gpt-5.6-terra"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        job = AIJob.objects.get(pk=resp.json()["job_id"])
        self.assertEqual(job.model_name, "gpt-5.6-terra")
        self.assertNotIn("_max_tokens", job.input_payload_json)
        args, _ = mock_enqueue.call_args
        self.assertEqual(decrypt_key(args[1]), "sk-mine")

    @override_settings(DEMO_LIVE_DAILY_LIMIT=2)
    def test_daily_limit_blocks_further_requests(self):
        ok1 = self._post_rules({"question": "Q1?", "access_code": "let-me-in"})
        ok2 = self._post_rules({"question": "Q2?", "access_code": "let-me-in"})
        blocked = self._post_rules({"question": "Q3?", "access_code": "let-me-in"})
        self.assertEqual(ok1.status_code, 202)
        self.assertEqual(ok2.status_code, 202)
        self.assertEqual(blocked.status_code, 429)


@override_settings(DEMO_LIVE_ENABLED=False)
class TestLiveDemoDisabled(TestCase):
    def test_access_code_rejected_when_live_demo_disabled(self):
        with patch("core.views.enqueue_ai_job"):
            resp = self.client.post(
                reverse("rules_job_create"),
                data=json.dumps({"question": "Q?", "access_code": "anything"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)


class TestTokenBudget(TestCase):
    def test_override_caps_below_default_only(self):
        from core.service.ai.service import _token_budget

        self.assertEqual(_token_budget(4000, 1500), 1500)   # caps
        self.assertEqual(_token_budget(4000, 8000), 4000)   # never raises above default
        self.assertEqual(_token_budget(4000, None), 4000)   # no override
        self.assertEqual(_token_budget(4000, 0), 4000)      # zero ignored


class TestLiveDemoPerFeatureCaps(TestCase):
    _DEFAULTS = {
        "rules": config.RULES_MAX_TOKENS,
        "strategy": config.STRATEGY_MAX_TOKENS,
        "move": config.MOVE_MAX_TOKENS,
        "tac_calc": config.TAC_CALC_MAX_TOKENS,
    }

    def test_each_feature_cap_is_reasoning_safe_and_below_default(self):
        for feature, default in self._DEFAULTS.items():
            cap = config.live_demo_max_tokens(feature)
            # Genuinely caps cost (at or below the normal budget)...
            self.assertLessEqual(cap, default, feature)
            # ...but never so low it starves a reasoning model into empty output.
            self.assertGreaterEqual(cap, 3000, feature)

    def test_heavy_features_actually_cap(self):
        # strategy/move must be strictly capped (this is the #2 fix).
        self.assertLess(config.live_demo_max_tokens("strategy"), config.STRATEGY_MAX_TOKENS)
        self.assertLess(config.live_demo_max_tokens("move"), config.MOVE_MAX_TOKENS)

    def test_unknown_feature_falls_back(self):
        self.assertEqual(
            config.live_demo_max_tokens("nope"), config.LIVE_DEMO_DEFAULT_MAX_TOKENS
        )
