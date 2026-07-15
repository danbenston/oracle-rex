"""Centralized configuration for the Oracle Rex AI service layer.

This is the single place to control:
  - which providers exist and which models belong to each,
  - the default/fallback model,
  - per-feature token limits,
  - request timeouts.

Views and the rest of the backend should never hard-code model names or
provider choices; they pass a model string through to the service layer, and
everything here decides how that maps to a concrete provider client.
"""

import os

# --- Providers -------------------------------------------------------------

OPENAI = "openai"
XAI = "xai"
ANTHROPIC = "anthropic"
GOOGLE = "google"

# Gemini runs on a key held by the server (not BYOK), so demo users get free live
# AI with no key of their own. Paste your key over <PLACEHOLDER> for local use, or
# (preferred, and required for production) set the GEMINI_API_KEY env var and do
# NOT commit the real key. The env var wins when set.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "<PLACEHOLDER>")


def gemini_api_key() -> str:
    """The server-held Gemini key. Used for all Google-model requests."""
    return GEMINI_API_KEY

# Models grouped by provider. Add new models here (and, if they belong to a new
# provider, add a client in ``clients/``) — nothing else needs to change.
#
# These are all current-generation reasoning ("thinking") models: they deliberate
# before answering, which improves quality but uses extra tokens and latency (see
# the token limits and ``*_REASONING_EFFORT`` settings below).
OPENAI_MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-nano"]
XAI_MODELS = ["grok-4.5", "grok-4.3"]
ANTHROPIC_MODELS = ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"]
# Claude models that accept the `effort` parameter. Not every Claude model does:
# Haiku 4.5 predates adaptive thinking and the API rejects `effort` for it, so
# sending one anyway would 400 the fast option that leads every feature group.
# Haiku needs no effort control regardless — its thinking is opt-in via the
# `thinking` parameter, which this app never sets, so it cannot silently spend a
# token budget on hidden reasoning the way Sonnet 5 can. Add a model here only
# after confirming it against the provider's effort docs.
ANTHROPIC_EFFORT_MODELS = {"claude-opus-4-8", "claude-sonnet-5"}
# Confirm the exact id against Google AI Studio and keep it identical to the
# matching option value in frontend/src/store/models.ts (a mismatch makes
# resolve_model fall back to the OpenAI default, which has no server key).
GEMINI_MODELS = ["gemini-3.1-flash-lite"]

# Reverse lookup: model id -> provider. Built once at import time.
PROVIDER_FOR_MODEL = {
    **{m: OPENAI for m in OPENAI_MODELS},
    **{m: XAI for m in XAI_MODELS},
    **{m: ANTHROPIC for m in ANTHROPIC_MODELS},
    **{m: GOOGLE for m in GEMINI_MODELS},
}

# Used when an unknown / deprecated model id comes in (e.g. the old "gpt-4"
# default the legacy frontend still sends). The cheapest/fastest reasoning model.
FALLBACK_MODEL = "gpt-5.4-nano"


def provider_for_model(model: str) -> str:
    """Return the provider that serves ``model``, falling back to OpenAI."""
    return PROVIDER_FOR_MODEL.get(model, OPENAI)


def resolve_model(model) -> str:
    """Normalize an incoming model id to one we actually support."""
    if model and model in PROVIDER_FOR_MODEL:
        return model
    return FALLBACK_MODEL


def anthropic_supports_effort(model: str) -> bool:
    """True if ``model`` accepts Claude's ``effort`` parameter."""
    return model in ANTHROPIC_EFFORT_MODELS


# --- Token limits (per feature) -------------------------------------------

# Reasoning models spend a large share of ``max_tokens`` on hidden thinking
# before producing visible output, so these limits cover BOTH the reasoning and
# the answer. They are deliberately much higher than the old non-reasoning
# values (500 / 5000 / 250) — set them too low and a reasoning model returns an
# empty response because the whole budget went to thinking.
RULES_MAX_TOKENS = 4000
STRATEGY_MAX_TOKENS = 12000
MOVE_MAX_TOKENS = 12000
# tac_calc pairs a long combat-rules system prompt with reasoning effort, so a
# lighter model could spend the whole 4000-token budget on hidden reasoning and
# return empty visible output — which the service layer surfaces as a "couldn't
# understand the response" MalformedResponseError. Raised to 8000 to leave room
# for the fixed-format answer after the reasoning. TAC_CALC_REASONING_EFFORT is
# now "low" for the same reason; the headroom stays as a belt-and-braces guard.
TAC_CALC_MAX_TOKENS = 8000

# --- Live-demo output caps (per feature) ----------------------------------

# Owner-paid private-live-demo requests cap output below the per-feature default
# to bound cost. The cap is PER FEATURE on purpose: these are reasoning models
# whose hidden thinking is billed against max_tokens, so a single low number
# (e.g. 2000) that's fine for the lightweight rules/calc features would starve
# the strategy/move features and make them return empty output. These values sit
# below each feature's normal budget (so they genuinely cap cost) but well above
# the point where a medium-effort reasoning model produces nothing.
LIVE_DEMO_MAX_TOKENS = {
    "rules": 3000,
    "strategy": 7000,
    "move": 7000,
    "tac_calc": 3000,
}
LIVE_DEMO_DEFAULT_MAX_TOKENS = 4000  # fallback for an unrecognized feature


def live_demo_max_tokens(feature_type: str) -> int:
    """Reasoning-safe output cap for a private-live-demo request, per feature."""
    return LIVE_DEMO_MAX_TOKENS.get(feature_type, LIVE_DEMO_DEFAULT_MAX_TOKENS)

# --- Reasoning effort (OpenAI + Anthropic) --------------------------------

# Controls how much these models deliberate before answering. Lower = faster
# and cheaper; higher = more thorough.
#
# Honored by OpenAI (GPT-5.x `reasoning_effort`) and Anthropic (Claude
# `effort`). xAI (Grok) and Gemini decide their own thinking depth and ignore
# this; their clients accept the argument for a uniform signature and drop it.
#
# Only the values common to both providers are used here: "low" and "medium".
# OpenAI additionally accepts "none" and (on gpt-5.6-sol) "max"; Anthropic
# accepts "high"/"xhigh"/"max". Anything outside a provider's set is that
# client's problem to translate — keep these to the shared subset.
#
# Anthropic's default is "high" when unset, which matters more than it looks:
# on Claude, effort covers ALL output tokens (thinking included) and thinking is
# billed against max_tokens. Left at the default, Sonnet 5 can spend an entire
# RULES_MAX_TOKENS budget thinking and return no visible text, which surfaces as
# a MalformedResponseError. Setting these explicitly is what prevents that.
RULES_REASONING_EFFORT = "low"          # quick factual lookups over retrieved passages
STRATEGY_REASONING_EFFORT = "medium"    # deep, multi-factor planning
MOVE_REASONING_EFFORT = "medium"        # tactical evaluation
# Fixed-format probability/combat arithmetic. "low" rather than "medium": the
# answer is short and templated, and higher effort mostly buys overthinking here
# (it was medium effort that drove the runaway reasoning behind the raised
# TAC_CALC_MAX_TOKENS above). Raise to "medium" if calc accuracy regresses.
TAC_CALC_REASONING_EFFORT = "low"

# --- Timeouts --------------------------------------------------------------

# Ceiling (seconds) on a single provider request. Now that AI work runs as an
# async job (Milestone 2) rather than inside the HTTP request, this no longer has
# to fit under a web-request deadline, so it's generous enough for slow reasoning
# models on big prompts (strategy/move use the largest token budgets). Tune with
# the AI_REQUEST_TIMEOUT env var; the worker/poller/reaper timeouts in settings
# are derived from the same value so they stay coherent.
DEFAULT_REQUEST_TIMEOUT = float(os.environ.get("AI_REQUEST_TIMEOUT", "180"))

# --- Rules retrieval (RAG) -------------------------------------------------

# Grounded Rules Q&A: retrieve Living Rules Reference passages and answer from
# them with real citations. Default ON; set RULES_RAG_ENABLED=0 to fall back to
# the pre-RAG recall path in one env var (the prompt stays rules_chat_v2 then).
RULES_RAG_ENABLED = os.environ.get("RULES_RAG_ENABLED", "1").lower() not in (
    "0", "false", "no", "",
)
# Number of passages to retrieve and place in the prompt. The Phase 1 eval shows
# recall@8 = 1.0 on the golden set, so 8 keeps every answer's evidence in context
# while staying cheap on input tokens.
RULES_RETRIEVAL_K = int(os.environ.get("RULES_RETRIEVAL_K", "8"))

# --- Prompt versions (per feature) ----------------------------------------

# Stamped onto every AIJob so logs/admin show which prompt produced a result.
# Bump the version when a prompt's wording changes materially; this seeds the
# optional prompt-versioning feature without extra plumbing.
PROMPT_VERSIONS = {
    "rules": "rules_chat_v3",
    "strategy": "strategic_plan_v1",
    "move": "tactical_move_v1",
    "tac_calc": "tactical_calculator_v3",
}


def prompt_version_for(feature_type: str) -> str:
    """Return the current prompt version string for a feature, or ''."""
    if feature_type == "rules":
        # The rules prompt has two forms; stamp the one actually used so a job's
        # provenance is honest about whether it was grounded.
        return "rules_chat_v3" if RULES_RAG_ENABLED else "rules_chat_v2"
    return PROMPT_VERSIONS.get(feature_type, "")


# Provider SDKs retry timeouts/5xx automatically (OpenAI defaults to 2 retries).
# For a slow reasoning model that exceeds the timeout, that turns one 90s wait
# into ~3x90s of silent hanging before failing — and each aborted attempt never
# completes on the provider side, so it doesn't even show up in their logs.
# Fail fast instead and let the user retry from the UI.
DEFAULT_MAX_RETRIES = 0
