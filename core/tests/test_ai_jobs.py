"""Tests for the async AI job pipeline (Milestone 2).

These exercise the worker task (``run_ai_job``), the BYOK key encryption, and the
create/status endpoints — all without a running qcluster or any real provider
call. ``get_chat`` is mocked at the service layer, so the job runs entirely
offline.
"""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core import jobs
from core.models import AIJob
from core.service.ai.crypto import decrypt_key, encrypt_key
from core.service.ai.errors import (
    InvalidAPIKeyError,
    MalformedResponseError,
    ProviderTimeoutError,
)
from core.service.ai.schemas import RulesAnswer


class TestCrypto(TestCase):
    def test_round_trip(self):
        token = encrypt_key("sk-secret-123")
        self.assertNotEqual(token, "sk-secret-123")
        self.assertEqual(decrypt_key(token), "sk-secret-123")

    def test_empty_key_is_empty_token(self):
        self.assertEqual(encrypt_key(""), "")
        self.assertEqual(decrypt_key(""), "")

    def test_tampered_token_decrypts_to_empty(self):
        token = encrypt_key("sk-secret-123")
        # Flip a character inside the ciphertext so the HMAC check fails.
        i = len(token) // 2
        flipped = "A" if token[i] != "A" else "B"
        tampered = token[:i] + flipped + token[i + 1:]
        self.assertEqual(decrypt_key(tampered), "")


class TestRunAiJob(TestCase):
    def _make_job(self, feature_type=AIJob.FeatureType.RULES, **kwargs):
        payload = kwargs.pop("input_payload_json", {"question": "Can I retreat?"})
        return AIJob.objects.create(
            feature_type=feature_type,
            input_payload_json=payload,
            model_name="gpt-5.6-terra",
            **kwargs,
        )

    def test_completed_job_stores_result(self):
        job = self._make_job()
        answer = RulesAnswer(answer="Yes, under these conditions.")
        result = jobs.service.RulesResult(answer=answer, passages=[])
        with patch.object(jobs.service, "get_rules_result", return_value=result):
            status = jobs.run_ai_job(str(job.id), encrypt_key("key"))

        job.refresh_from_db()
        self.assertEqual(status, AIJob.Status.COMPLETED)
        self.assertEqual(job.status, AIJob.Status.COMPLETED)
        self.assertEqual(job.result_payload_json["answer"], "Yes, under these conditions.")
        self.assertIn("structured", job.result_payload_json)
        self.assertIn("passages", job.result_payload_json)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.completed_at)

    def test_worker_decrypts_byok_key(self):
        job = self._make_job()
        captured = {}

        def fake(question, api_key, model=None, max_tokens=None, persona=None):
            captured["api_key"] = api_key
            return jobs.service.RulesResult(answer=RulesAnswer(answer="ok"), passages=[])

        with patch.object(jobs.service, "get_rules_result", side_effect=fake):
            jobs.run_ai_job(str(job.id), encrypt_key("sk-live-xyz"))

        self.assertEqual(captured["api_key"], "sk-live-xyz")

    def test_timeout_error_maps_to_timeout_status(self):
        job = self._make_job()
        with patch.object(
            jobs.service, "get_rules_result", side_effect=ProviderTimeoutError()
        ):
            jobs.run_ai_job(str(job.id), encrypt_key("key"))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.TIMEOUT)
        self.assertTrue(job.error_message)
        self.assertIsNone(job.result_payload_json)

    def test_malformed_error_maps_to_validation_failed(self):
        job = self._make_job()
        with patch.object(
            jobs.service, "get_rules_result", side_effect=MalformedResponseError()
        ):
            jobs.run_ai_job(str(job.id), encrypt_key("key"))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.VALIDATION_FAILED)

    def test_invalid_key_maps_to_failed(self):
        job = self._make_job()
        with patch.object(
            jobs.service, "get_rules_result", side_effect=InvalidAPIKeyError()
        ):
            jobs.run_ai_job(str(job.id), encrypt_key("bad"))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.FAILED)

    def test_missing_job_returns_failed(self):
        import uuid
        self.assertEqual(
            jobs.run_ai_job(str(uuid.uuid4()), ""), AIJob.Status.FAILED
        )


class _FakeTask:
    """Stand-in for django_q's Task object passed to the completion hook."""

    def __init__(self, job_id, success, result="killed"):
        self.id = "fake-task"
        self.args = (str(job_id), "")
        self.success = success
        self.result = result


class TestCompletionHook(TestCase):
    def test_hook_marks_stuck_running_job_as_timeout(self):
        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.RUNNING,
        )
        jobs.ai_job_complete(_FakeTask(job.id, success=False))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.TIMEOUT)

    def test_hook_leaves_terminal_job_untouched(self):
        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.COMPLETED,
            result_payload_json={"answer": "done"},
        )
        jobs.ai_job_complete(_FakeTask(job.id, success=False))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.COMPLETED)

    def test_hook_ignores_successful_task(self):
        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.RUNNING,
        )
        jobs.ai_job_complete(_FakeTask(job.id, success=True))

        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.RUNNING)


class TestJobEndpoints(TestCase):
    def test_create_rules_job_returns_job_id_and_enqueues(self):
        with patch("core.views.enqueue_ai_job") as mock_enqueue:
            resp = self.client.post(
                reverse("rules_job_create"),
                data=json.dumps({"question": "Can I retreat?", "api_key": "sk-x", "model": "gpt-5.6-terra"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertIn("job_id", body)
        self.assertEqual(body["status"], AIJob.Status.QUEUED)

        job = AIJob.objects.get(pk=body["job_id"])
        self.assertEqual(job.feature_type, AIJob.FeatureType.RULES)
        self.assertEqual(job.model_name, "gpt-5.6-terra")
        self.assertEqual(job.prompt_version, "rules_chat_v3")

        # Key is encrypted into the enqueue arg, and never stored on the row.
        mock_enqueue.assert_called_once()
        args, _ = mock_enqueue.call_args
        job_id_arg, encrypted_arg = args[0], args[1]
        self.assertEqual(job_id_arg, str(job.id))
        self.assertNotIn("sk-x", json.dumps(job.input_payload_json))
        self.assertEqual(decrypt_key(encrypted_arg), "sk-x")

    def test_create_rules_job_requires_question(self):
        with patch("core.views.enqueue_ai_job"):
            resp = self.client.post(
                reverse("rules_job_create"),
                data=json.dumps({"question": ""}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_create_strategy_job_requires_faction(self):
        with patch("core.views.enqueue_ai_job"):
            resp = self.client.post(
                reverse("strategy_job_create"),
                data=json.dumps({"game_json": {"a": 1}}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_status_endpoint_reports_job(self):
        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.TAC_CALC,
            input_payload_json={"force_data": {"friendly_fleet": {}}},
            status=AIJob.Status.COMPLETED,
            result_payload_json={"calc_results": "60% win"},
        )
        resp = self.client.get(reverse("ai_job_status", args=[job.id]))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], AIJob.Status.COMPLETED)
        self.assertTrue(body["is_terminal"])
        self.assertEqual(body["result"]["calc_results"], "60% win")

    def test_status_endpoint_unknown_job_is_404(self):
        import uuid
        resp = self.client.get(reverse("ai_job_status", args=[uuid.uuid4()]))
        self.assertEqual(resp.status_code, 404)


class TestEnqueueDispatch(TestCase):
    @override_settings(AI_JOB_BACKEND="thread")
    def test_thread_backend_submits_to_pool(self):
        from unittest.mock import MagicMock

        fake_pool = MagicMock()
        with patch.object(jobs, "_thread_pool", return_value=fake_pool):
            jobs.enqueue_ai_job("job-123", "token")
        fake_pool.submit.assert_called_once_with(
            jobs._run_in_thread, "job-123", "token"
        )

    @override_settings(AI_JOB_BACKEND="django_q")
    def test_django_q_backend_enqueues_with_hook(self):
        with patch("django_q.tasks.async_task") as mock_async:
            jobs.enqueue_ai_job("job-123", "token")
        mock_async.assert_called_once()
        args, kwargs = mock_async.call_args
        self.assertEqual(args[0], "core.jobs.run_ai_job")
        self.assertEqual(args[1], "job-123")
        self.assertEqual(kwargs["hook"], "core.jobs.ai_job_complete")


class TestStaleReaper(TestCase):
    def test_stale_running_job_is_reaped_to_timeout(self):
        from datetime import timedelta
        from django.utils import timezone

        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(seconds=jobs.STALE_RUNNING_SECONDS + 60),
        )
        jobs.reap_if_stale(job)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.TIMEOUT)
        self.assertTrue(job.error_message)

    def test_fresh_running_job_is_left_alone(self):
        from django.utils import timezone

        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.RUNNING,
            started_at=timezone.now(),
        )
        jobs.reap_if_stale(job)
        job.refresh_from_db()
        self.assertEqual(job.status, AIJob.Status.RUNNING)

    def test_status_endpoint_reaps_stale_job(self):
        from datetime import timedelta
        from django.utils import timezone

        job = AIJob.objects.create(
            feature_type=AIJob.FeatureType.RULES,
            input_payload_json={"question": "x"},
            status=AIJob.Status.RUNNING,
            started_at=timezone.now() - timedelta(seconds=jobs.STALE_RUNNING_SECONDS + 60),
        )
        resp = self.client.get(reverse("ai_job_status", args=[job.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], AIJob.Status.TIMEOUT)
