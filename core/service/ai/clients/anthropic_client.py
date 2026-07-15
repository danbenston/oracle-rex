"""Anthropic (Claude) chat client (via LangChain).

``langchain_anthropic`` is imported lazily inside ``build_chat`` so the rest of
the app keeps working before the package is installed / an API key is set up.
Install with: ``pip install langchain-anthropic`` (already listed in
requirements.txt).
"""

from .. import config
from ..errors import ProviderError


def build_chat(model_name: str, api_key: str, max_tokens: int, reasoning_effort: str = None):
    """Build a Claude chat client.

    ``reasoning_effort`` maps to Claude's ``effort`` parameter. Forwarding it is
    not optional tuning: effort governs every output token including thinking,
    and thinking is billed against ``max_tokens``. Claude defaults to "high"
    when effort is unset, so an unset effort on a current model can burn the
    whole budget thinking and return no visible text.

    It is only sent to models that accept it (see
    ``config.anthropic_supports_effort``) — the API rejects ``effort`` outright
    on models that predate it, so sending it unconditionally would turn a
    working model into a hard 400.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise ProviderError(
            "Anthropic (Claude) support is not installed on the server yet. "
            "Choose an OpenAI or Grok model, or install 'langchain-anthropic'.",
            detail=str(exc),
        )

    effort = reasoning_effort if config.anthropic_supports_effort(model_name) else None

    return ChatAnthropic(
        model=model_name,
        api_key=api_key,
        max_tokens=max_tokens,
        effort=effort,
        timeout=config.DEFAULT_REQUEST_TIMEOUT,
        max_retries=config.DEFAULT_MAX_RETRIES,
    )
