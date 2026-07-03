"""Public AI service layer.

Every AI-powered feature calls one of the functions here. Each function:

  * validates its inputs,
  * builds the prompt (from ``prompts/``),
  * builds the provider client (from ``clients/``),
  * runs the request with structured-output validation where applicable,
  * and converts any provider failure into a clear ``AIServiceError``.

Structured features (rules, strategy, move) return a validated Pydantic object
and gracefully fall back to plain text wrapped in the same schema when a model
cannot produce structured output. The battle calculator returns a rigid,
fixed-format text block and is returned as plain text.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import config, personas
from .clients import get_chat
from .errors import (
    AIServiceError,
    InputValidationError,
    InvalidAPIKeyError,
    MalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    QuotaExceededError,
    classify_provider_error,
)
from .prompts import (
    rules_chat,
    strategic_plan as strategic_plan_prompt,
    tactical_calculator,
    tactical_move as tactical_move_prompt,
)
from .schemas import RulesAnswer, StrategicPlan, TacticalMove

logger = logging.getLogger(__name__)

# Errors that re-running as plain text would only hit again, so we never fall
# back on these — we surface them straight away.
_NON_RECOVERABLE = (
    InvalidAPIKeyError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    QuotaExceededError,
)


# --- internal helpers ------------------------------------------------------

def _classify_and_log(exc: Exception, feature: str) -> AIServiceError:
    """Classify a provider exception and log its real cause for debugging."""
    err = classify_provider_error(exc)
    # ``detail`` carries the raw provider message — log it so the actual cause
    # (e.g. OpenAI 'insufficient_quota') is visible even though the user only
    # sees the friendly message.
    logger.warning(
        "AI provider error in %s: %s | %s",
        feature,
        type(err).__name__,
        err.detail or exc,
    )
    return err


def _invoke_plain(chat, messages, feature: str) -> str:
    """Invoke a chat model and return its text content, or raise AIServiceError."""
    # One log line per real provider call, so the count per user action is
    # visible (e.g. to diagnose quota burn: a structured feature that falls back
    # logs both a 'structured' and a 'plain' call).
    logger.info("Provider invoke: feature=%s mode=plain", feature)
    try:
        response = chat.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - re-classified into AIServiceError
        raise _classify_and_log(exc, feature)

    content = getattr(response, "content", response)
    if not isinstance(content, str) or not content.strip():
        raise MalformedResponseError(detail=f"Empty/invalid content: {content!r}")
    return content


def _invoke_structured(chat, messages, schema, feature: str):
    """Run a structured-output request, falling back to plain text on failure."""
    logger.info("Provider invoke: feature=%s mode=structured", feature)
    try:
        structured = chat.with_structured_output(schema)
        result = structured.invoke(messages)
    except Exception as exc:  # noqa: BLE001
        classified = _classify_and_log(exc, feature)
        if isinstance(classified, _NON_RECOVERABLE):
            raise classified
        # Structured output unsupported or unparseable for this model — fall
        # back to a plain-text call so the feature still works.
        logger.warning(
            "Structured output failed for %s (%s); falling back to plain text.",
            feature,
            type(exc).__name__,
        )
        text = _invoke_plain(chat, messages, feature)
        return schema.fallback_from_text(text)

    # Normalize whatever the provider handed back into a validated schema object.
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        try:
            return schema.model_validate(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Validation failed for %s: %s", feature, exc)
            raise MalformedResponseError(detail=str(exc))
    logger.warning("Unexpected structured result type for %s: %r", feature, type(result))
    raise MalformedResponseError(detail=f"Unexpected result type: {type(result)!r}")


def _require(condition: bool, message: str):
    if not condition:
        raise InputValidationError(message)


# --- public API ------------------------------------------------------------

def _token_budget(default: int, override) -> int:
    """Resolve the max-token budget for a call.

    ``override`` (when a positive int) caps output below the per-feature default
    — used by the private live-demo path to bound owner-paid cost. A None/0/
    larger value leaves the generous default in place.
    """
    if override and 0 < int(override) < default:
        return int(override)
    return default


@dataclass
class RulesResult:
    """A rules answer plus the retrieved passages it was grounded in.

    ``passages`` (``{rule_id, topic, text, score}``) go into the job payload so
    the frontend can show the exact cited rule text with no second request; the
    quoted text therefore always comes from our index, never the model.
    """

    answer: RulesAnswer
    passages: List[Dict[str, Any]] = field(default_factory=list)


def _retrieve_rules_passages(question: str) -> List[Dict[str, Any]]:
    """Retrieve LRR passages for a question, or [] when RAG is off/unavailable.

    Retrieval failures degrade gracefully to the ungrounded recall path rather
    than failing the request — a missing index (e.g. not built on a fresh deploy)
    should never take the feature down.
    """
    if not config.RULES_RAG_ENABLED:
        return []
    try:
        from core.service.rules_index import RulesIndexError, retrieve

        hits = retrieve(question, k=config.RULES_RETRIEVAL_K)
    except Exception as exc:  # noqa: BLE001 - any retrieval issue -> ungrounded
        logger.warning("Rules retrieval unavailable; answering ungrounded: %s", exc)
        return []
    return [
        {"rule_id": h.rule_id, "topic": h.topic, "text": h.text, "score": round(h.score, 3)}
        for h in hits
    ]


def _validate_citations(answer: RulesAnswer, passages: List[Dict[str, Any]]) -> RulesAnswer:
    """Drop cited rule_ids that were not actually retrieved; set ``grounded``.

    Deterministic anti-hallucination check: a citation is only trustworthy if it
    points at a rule we placed in the prompt. ``grounded`` is derived from the
    surviving citations, not the model's self-report.
    """
    retrieved_ids = {p["rule_id"] for p in passages}
    valid = [c for c in answer.citations if c.rule_id in retrieved_ids]
    dropped = [c.rule_id for c in answer.citations if c.rule_id not in retrieved_ids]
    if dropped:
        logger.warning(
            "Dropped %d cited rule_id(s) not in the retrieved set: %s",
            len(dropped), dropped,
        )
    answer.citations = valid
    answer.grounded = bool(valid)
    return answer


def get_rules_result(
    question: str, api_key: str, model: str = None, max_tokens: int = None,
    persona: str = None,
) -> RulesResult:
    """Grounded Rules Q&A: retrieve LRR passages, answer from them, validate the
    citations, and return both the answer and the passages used."""
    _require(bool(question and question.strip()), "No question was provided.")
    passages = _retrieve_rules_passages(question)
    chat = get_chat(
        model, api_key,
        _token_budget(config.RULES_MAX_TOKENS, max_tokens),
        config.RULES_REASONING_EFFORT,
    )
    messages = personas.apply_persona(
        rules_chat.build_messages(question, passages or None), persona
    )
    answer = _invoke_structured(chat, messages, RulesAnswer, "rules")
    answer = _validate_citations(answer, passages)
    return RulesResult(answer=answer, passages=passages)


def get_rules_response(
    question: str, api_key: str, model: str = None, max_tokens: int = None,
    persona: str = None,
) -> RulesAnswer:
    """Backward-compatible wrapper returning just the answer (no passages)."""
    return get_rules_result(question, api_key, model, max_tokens, persona).answer


def get_strategy_response(
    game_json: Dict[str, Any], player_faction: str, api_key: str = None,
    model: str = None, max_tokens: int = None, persona: str = None,
) -> StrategicPlan:
    _require(bool(game_json), "No board state was provided.")
    _require(bool(player_faction), "No faction was selected.")
    chat = get_chat(
        model, api_key,
        _token_budget(config.STRATEGY_MAX_TOKENS, max_tokens),
        config.STRATEGY_REASONING_EFFORT,
    )
    messages = personas.apply_persona(
        strategic_plan_prompt.build_messages(game_json, player_faction), persona
    )
    return _invoke_structured(chat, messages, StrategicPlan, "strategy")


def get_move_response(
    game_json: Dict[str, Any], player_faction: str, api_key: str = None,
    model: str = None, max_tokens: int = None, persona: str = None,
) -> TacticalMove:
    _require(bool(game_json), "No board state was provided.")
    _require(bool(player_faction), "No faction was selected.")
    chat = get_chat(
        model, api_key,
        _token_budget(config.MOVE_MAX_TOKENS, max_tokens),
        config.MOVE_REASONING_EFFORT,
    )
    messages = personas.apply_persona(
        tactical_move_prompt.build_messages(game_json, player_faction), persona
    )
    return _invoke_structured(chat, messages, TacticalMove, "move")


def get_tac_calc_response(
    force_data: Dict[str, Any], simulation: Dict[str, Any] = None,
    api_key: str = None, model: str = None, max_tokens: int = None,
) -> str:
    _require(bool(force_data), "No fleet data was provided.")
    chat = get_chat(
        model, api_key,
        _token_budget(config.TAC_CALC_MAX_TOKENS, max_tokens),
        config.TAC_CALC_REASONING_EFFORT,
    )
    messages = tactical_calculator.build_messages(force_data, simulation)
    return _invoke_plain(chat, messages, "tac_calc")


__all__ = [
    "AIServiceError",
    "RulesResult",
    "get_rules_response",
    "get_rules_result",
    "get_strategy_response",
    "get_move_response",
    "get_tac_calc_response",
]
