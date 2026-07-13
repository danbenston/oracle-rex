"""promptfoo custom Python provider that calls the REAL Oracle Rex pipeline.

Why a custom provider instead of prompt text in YAML: the prompts here are
assembled in code (``core/service/ai/prompts/*`` + ``personas.apply_persona``,
and later retrieval), so duplicating that text into promptfoo config would drift
the moment a prompt version bumps. This adapter instead does ``django.setup()``
and routes each test case through the same ``get_rules_response`` /
``get_strategy_response`` / ``get_move_response`` / ``get_tac_calc_response``
functions the live service uses — so an eval validates the actual behavior
behind a prompt version, not a copy of it.

promptfoo contract (custom Python provider): this module exposes ``call_api``:

    def call_api(prompt, options, context) -> {"output": ...} | {"error": ...}

  * ``options["config"]`` is the provider ``config`` block from the promptfoo
    YAML (feature, model, persona, ...).
  * ``context["vars"]`` are the per-test variables (question, game_json, ...).

The output is a JSON string of the structured result PLUS a ``_meta`` block
(schema-valid flag, prompt version, whether the plain-text fallback path was
taken, model/feature/persona). Assertions can therefore check pipeline facts,
not just answer text. Errors from the service layer are surfaced as
``{"error": ...}`` so a failing case reads as a failure, never a false pass.

Default provider for eval runs is the server-held Gemini model (same cost
ceiling as the live demo). Cross-provider matrix runs pass a BYOK model id and
key via the environment — see evals/README.md.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

# --- one-time Django setup -------------------------------------------------
#
# The provider file lives at evals/providers/oracle_rex_provider.py, so the
# repo root (which holds manage.py and the ``oracle-rex`` settings package) is
# two directories up. SKIP_DB_STARTUP=1 avoids the import-time session DB
# rebuild in core/util/__init__.py — none of the service functions here touch
# the DB (they serialize the game_json/force_data passed in the test vars), so
# skipping it keeps runs fast and side-effect-free.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oracle-rex.settings")
os.environ.setdefault("SKIP_DB_STARTUP", "1")

_DJANGO_READY = False


def _ensure_django():
    global _DJANGO_READY
    if _DJANGO_READY:
        return
    import django

    django.setup()
    _DJANGO_READY = True


# --- fallback detection ----------------------------------------------------
#
# The service layer guarantees a validated schema object or raises: when a model
# cannot produce structured output it logs a warning and returns the plain-text
# result wrapped via ``schema.fallback_from_text``. That path is invisible to
# the caller, so we listen for the log line to report ``fallback_used`` — an
# eval wants to know a "passing" answer actually came from structured output and
# not the degraded fallback.
_FALLBACK_MARKER = "falling back to plain text"


class _FallbackWatcher(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.tripped = False

    def emit(self, record):
        if _FALLBACK_MARKER in record.getMessage():
            self.tripped = True


@contextmanager
def _watch_fallback():
    watcher = _FallbackWatcher()
    logger = logging.getLogger("core.service.ai.service")
    logger.addHandler(watcher)
    try:
        yield watcher
    finally:
        logger.removeHandler(watcher)


# --- feature dispatch ------------------------------------------------------


def _call_feature(feature, model, persona, vars_):
    """Route to the real service function and return (payload_dict, meta_extra).

    ``payload_dict`` is the JSON-serializable structured result; ``meta_extra``
    holds per-feature metadata (currently unused but kept for symmetry).
    """
    from core.service.ai import service

    # Gemini runs on the server-held key inside get_chat; BYOK models read their
    # key from the environment (matrix runs). Empty string is fine for Gemini.
    api_key = _api_key_for(model)

    if feature == "rules":
        question = _require_var(vars_, "question", feature)
        result = service.get_rules_response(
            question, api_key=api_key, model=model, persona=persona
        )
        return result.model_dump(), {}

    if feature == "strategy":
        game_json = _require_var(vars_, "game_json", feature)
        faction = _require_var(vars_, "player_faction", feature)
        result = service.get_strategy_response(
            _as_obj(game_json), faction, api_key=api_key, model=model, persona=persona
        )
        return result.model_dump(), {}

    if feature == "move":
        game_json = _require_var(vars_, "game_json", feature)
        faction = _require_var(vars_, "player_faction", feature)
        result = service.get_move_response(
            _as_obj(game_json), faction, api_key=api_key, model=model, persona=persona
        )
        return result.model_dump(), {}

    if feature == "tac_calc":
        force_data = _require_var(vars_, "force_data", feature)
        simulation = vars_.get("simulation")
        result = service.get_tac_calc_response(
            _as_obj(force_data),
            simulation=_as_obj(simulation) if simulation is not None else None,
            api_key=api_key,
            model=model,
        )
        # tac_calc is plain text (rigid narrated format), not a schema object.
        return {"text": result}, {}

    raise ValueError(
        f"Unknown feature {feature!r}. Expected one of: "
        "rules, strategy, move, tac_calc."
    )


def _api_key_for(model: str) -> str:
    """BYOK key from the environment for matrix runs; '' for server-Gemini.

    Gemini's key is injected server-side in ``get_chat`` regardless of what we
    pass, so an empty string is correct for the default path. For non-Gemini
    models the provider must supply the key; we read the conventional per-
    provider env vars promptfoo/users already set.
    """
    from core.service.ai import config

    provider = config.provider_for_model(config.resolve_model(model))
    if provider == config.GOOGLE:
        return ""
    env_name = {
        config.OPENAI: "OPENAI_API_KEY",
        config.XAI: "XAI_API_KEY",
        config.ANTHROPIC: "ANTHROPIC_API_KEY",
    }.get(provider, "OPENAI_API_KEY")
    return os.environ.get(env_name, "")


def _as_obj(value):
    """Accept a var that is already a dict/list, or a JSON string."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


def _require_var(vars_, name, feature):
    if name not in vars_ or vars_[name] in (None, ""):
        raise ValueError(f"Feature {feature!r} requires var {name!r} in the test case.")
    return vars_[name]


# --- promptfoo entry point -------------------------------------------------


def call_api(prompt, options, context):
    options = options or {}
    context = context or {}
    cfg = options.get("config") or {}
    vars_ = context.get("vars") or {}

    feature = cfg.get("feature", "rules")
    # Model may be set at the provider level (cfg) or overridden per test (vars).
    model = vars_.get("model") or cfg.get("model")
    persona = vars_.get("persona") or cfg.get("persona") or "default"

    try:
        _ensure_django()
        from core.service.ai import config
        from core.service.ai.errors import AIServiceError

        resolved_model = config.resolve_model(model)

        with _watch_fallback() as watcher:
            payload, _meta_extra = _call_feature(feature, model, persona, vars_)

        payload["_meta"] = {
            "feature": feature,
            "model": resolved_model,
            "persona": persona,
            "prompt_version": config.prompt_version_for(feature),
            # We reached here with a payload, so the service returned a validated
            # object (or the rigid tac_calc text) — the schema is satisfied.
            "schema_valid": True,
            # True when the model could not produce structured output and the
            # service degraded to plain text wrapped in the schema.
            "fallback_used": watcher.tripped,
        }
        return {"output": json.dumps(payload, ensure_ascii=False)}

    except Exception as exc:  # noqa: BLE001 - report every failure to promptfoo
        # AIServiceError carries a user-facing message; anything else (config,
        # setup, bad test var) is surfaced verbatim so the case fails loudly.
        try:
            from core.service.ai.errors import AIServiceError

            if isinstance(exc, AIServiceError):
                detail = getattr(exc, "detail", "") or str(exc)
                return {"error": f"{type(exc).__name__}: {detail}"}
        except Exception:  # noqa: BLE001
            pass
        return {"error": f"{type(exc).__name__}: {exc}"}
