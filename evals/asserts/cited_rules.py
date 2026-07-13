"""Tier B assertion: the grounded answer cites the rules it should.

The retrieval eval (Tier A, pytest) already proves the right rule is retrievable;
this checks the *answer* actually used it. For each golden case, ``vars`` carry
``expected_rule_ids`` (the authoritative rules) and ``grounded_expected``. This
assert then requires:

  * grounded case  -> ``grounded: true`` AND at least one expected rule_id in the
    response's ``citations`` (post-validated + inline-harvested by the service,
    so a cited id is always real);
  * ungrounded case -> ``grounded: false`` and no citations (the honest
    out-of-corpus path).

Deterministic and free once the response exists. It is strict on purpose — the
harness is for reading the delta between runs, so a model that cites a
neighbouring-but-wrong rule should show up.
"""

from __future__ import annotations

import json


def _fail(reason: str) -> dict:
    return {"pass": False, "score": 0.0, "reason": reason}


def _pass(reason: str) -> dict:
    return {"pass": True, "score": 1.0, "reason": reason}


def _expected_ids(raw) -> list:
    """Parse expected rule ids from a promptfoo var.

    The generator passes a space/comma-joined string (promptfoo would otherwise
    expand a list var into multiple test cases), but accept a real list too so
    the assert is robust to either shape.
    """
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [t for t in str(raw or "").replace(",", " ").split() if t]


def _as_bool(raw, default: bool = True) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def get_assert(output, context):
    context = context or {}
    vars_ = context.get("vars") or {}
    expected = _expected_ids(vars_.get("expected_rule_ids"))
    grounded_expected = _as_bool(vars_.get("grounded_expected", True))

    try:
        payload = json.loads(output) if isinstance(output, str) else output
    except (ValueError, TypeError) as exc:
        return _fail(f"output is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return _fail("output is not a JSON object")

    grounded = bool(payload.get("grounded"))
    cited = {c.get("rule_id") for c in (payload.get("citations") or []) if isinstance(c, dict)}

    if not grounded_expected:
        if grounded or cited:
            return _fail(f"expected an ungrounded answer, got grounded={grounded} cites={sorted(cited)}")
        return _pass("correctly ungrounded (no citations)")

    if not grounded:
        return _fail(f"expected a grounded answer but grounded=false (cites={sorted(cited)})")
    if not expected:
        # No expected ids to check against; grounded with any citation is enough.
        return _pass("grounded") if cited else _fail("grounded but no citations")

    hits = [e for e in expected if e in cited]
    if not hits:
        return _fail(f"none of the expected rule_ids {expected} were cited (cited: {sorted(cited)})")
    return _pass(f"cited expected rule(s) {hits}")
