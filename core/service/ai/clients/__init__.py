"""Provider client factory.

``get_chat`` is the one place that turns a model id + api key into a concrete
LangChain chat model. Provider selection is driven entirely by ``config``.
"""

from .. import config
from ..errors import MissingAPIKeyError
from . import anthropic_client, gemini_client, openai_client, xai_client

_BUILDERS = {
    config.OPENAI: openai_client.build_chat,
    config.XAI: xai_client.build_chat,
    config.ANTHROPIC: anthropic_client.build_chat,
    config.GOOGLE: gemini_client.build_chat,
}


def get_chat(model: str, api_key: str, max_tokens: int, reasoning_effort: str = None):
    """Build a chat model for ``model``, validating the API key is present.

    ``reasoning_effort`` applies to OpenAI and Anthropic models; the xAI and
    Gemini clients accept and ignore it (they manage their own thinking). The
    Anthropic client sends it only to the Claude models that accept it.

    Google (Gemini) models run on the server-held key (config.gemini_api_key()),
    not a per-request BYOK key, so the request never has to carry one.
    """
    resolved = config.resolve_model(model)
    provider = config.provider_for_model(resolved)
    if provider == config.GOOGLE:
        api_key = config.gemini_api_key()

    if not api_key:
        raise MissingAPIKeyError()

    builder = _BUILDERS[provider]
    return builder(resolved, api_key, max_tokens, reasoning_effort)
