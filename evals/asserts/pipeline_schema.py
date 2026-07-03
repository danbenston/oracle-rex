"""Deterministic promptfoo assertion: the response is a well-formed pipeline
result, not just some text.

promptfoo calls ``get_assert(output, context)`` for a ``type: python`` assert.
``output`` is the provider's output string (our JSON blob); ``context['vars']``
are the test vars. We return a GradingResult dict so the reason shows up in the
promptfoo report.

This is the "free once the response exists" check the harness leans on: it costs
no tokens and catches the failures that matter most — malformed output, the
schema not validating, an empty answer, a missing prompt-version stamp. Per-case
key-fact ``contains``/``not-contains`` checks live in the case files; this assert
owns the structural contract.

Set ``allow_fallback: false`` in a case's vars to also fail when the model could
not produce native structured output and the service degraded to plain text.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the app importable so we can re-validate against the real Pydantic schema
# (belt-and-suspenders on top of the provider's own validation). Plain schema
# modules, so no django.setup / DB rebuild is needed. This assert runs in its
# own promptfoo subprocess, so it must put the repo root on sys.path itself
# (evals/asserts -> evals -> repo root == parents[2]).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oracle-rex.settings")
os.environ.setdefault("SKIP_DB_STARTUP", "1")


def _fail(reason: str) -> dict:
    return {"pass": False, "score": 0.0, "reason": reason}


def _pass(reason: str) -> dict:
    return {"pass": True, "score": 1.0, "reason": reason}


# Feature -> the field that must be non-empty for the answer to be useful, plus
# the real Pydantic schema to re-validate (None for the plain-text tac_calc).
def _schema_for(feature: str):
    if feature == "rules":
        from core.service.ai.schemas.rules_answer import RulesAnswer

        return RulesAnswer, "answer"
    if feature == "strategy":
        from core.service.ai.schemas.strategic_plan import StrategicPlan

        return StrategicPlan, "summary"
    if feature == "move":
        from core.service.ai.schemas.tactical_move import TacticalMove

        return TacticalMove, "summary"
    if feature == "tac_calc":
        return None, "text"
    return None, None


def get_assert(output, context):
    context = context or {}
    vars_ = context.get("vars") or {}
    allow_fallback = vars_.get("allow_fallback", True)

    try:
        payload = json.loads(output) if isinstance(output, str) else output
    except (ValueError, TypeError) as exc:
        return _fail(f"Output is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return _fail(f"Output is not a JSON object (got {type(payload).__name__}).")

    meta = payload.get("_meta") or {}
    feature = meta.get("feature", vars_.get("feature", "rules"))

    if not meta.get("schema_valid"):
        return _fail("Pipeline reported schema_valid=false (no validated result).")
    if not meta.get("prompt_version"):
        return _fail("Missing prompt_version stamp in _meta.")

    schema, answer_field = _schema_for(feature)
    if answer_field:
        value = payload.get(answer_field)
        if not (isinstance(value, str) and value.strip()):
            return _fail(f"Empty/missing answer field {answer_field!r} for {feature}.")

    # Re-validate against the real schema (structured features only).
    if schema is not None:
        body = {k: v for k, v in payload.items() if k != "_meta"}
        try:
            schema.model_validate(body)
        except Exception as exc:  # noqa: BLE001 - any validation error is a fail
            return _fail(f"{schema.__name__} did not validate: {exc}")

    if meta.get("fallback_used") and not allow_fallback:
        return _fail(
            "Model fell back to plain text (allow_fallback=false): no native "
            "structured output."
        )

    note = " (plain-text fallback path)" if meta.get("fallback_used") else ""
    return _pass(
        f"{feature} response valid; prompt_version={meta.get('prompt_version')}{note}."
    )
