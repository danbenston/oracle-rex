# Oracle Rex — AI Service Layer

Centralized backend layer for all AI-powered features (Milestone 1 of the
upgrade plan). Views never call provider SDKs directly; they call
`service.py`, which owns prompts, provider selection, validation, and errors.

## AI call-site audit

There are four AI-powered features. Each is backed by one service function,
dispatched by the async worker (`core/jobs.py`, Milestone 2) and reached through
a `/api/jobs/<feature>/` create endpoint:

| Feature            | Create endpoint (`core/views.py`) | Service fn (`ai/service.py`) | Prompt (`ai/prompts/`)     | Schema (`ai/schemas/`) |
| ------------------ | --------------------------------- | ---------------------------- | -------------------------- | ---------------------- |
| Rules Q&A          | `rules_job_create`                | `get_rules_response`         | `rules_chat.py`            | `RulesAnswer`          |
| Strategy suggester | `strategy_job_create`             | `get_strategy_response`      | `strategic_plan.py`        | `StrategicPlan`        |
| Move suggester     | `move_job_create`                 | `get_move_response`          | `tactical_move.py`         | `TacticalMove`         |
| Battle calculator  | `tactical_job_create`             | `get_tac_calc_response`      | `tactical_calculator.py`   | — (rigid text format)  |

Each create endpoint resolves credentials via `_resolve_ai_credentials`: an
`access_code` in the request body unlocks the private live demo (owner key +
cheap `DEMO_LIVE_MODEL` + a `max_tokens` output cap + a daily request limit);
otherwise the user's BYOK key and chosen model are used. The cap is threaded into
the job as an internal `_max_tokens` payload directive and applied in `service.py`
via `_token_budget` (it only ever caps *below* the per-feature default).

**Demo mode (Milestone 3).** The public, no-key experience lives in `core/demo/`
(sample scenarios + pregenerated responses). `/api/demo/run/` serves a cached
response as a *pre-completed* `AIJob`, so the same polling frontend renders it
with no provider call — demo mode can't run up owner cost. `/api/demo/catalog/`
drives the one-click sample entries; `/api/demo/status/` reports whether the
private live demo is configured.

### Before this milestone

- Each feature had its own `make_*_chain` module that mixed prompt text, model
  wiring, and response parsing.
- Provider/model selection and `max_tokens` lived in `ai_service.py` and were
  duplicated per feature.
- Responses were freeform strings with no validation.
- Errors (missing/invalid key, timeout, rate limit, bad output) surfaced as
  `Unexpected error: <stack trace text>` with HTTP 500.

## Architecture

```
core/service/ai/
  config.py        providers, model registry, token limits, timeout (single source of truth)
  errors.py        AIServiceError hierarchy + classify_provider_error()
  clients/         get_chat() factory -> openai_client / xai_client / anthropic_client
  prompts/         build_messages() per feature (all prompt text lives here)
  schemas/         Pydantic models with to_display_text() + fallback_from_text()
  service.py       public functions; validation, structured output, error mapping
```

### Providers and models

Supported providers: **OpenAI**, **xAI (Grok)**, **Anthropic (Claude)**. Add a
model to the relevant list in `config.py`; provider selection is derived from
the model id automatically. The Anthropic client lazily imports
`langchain_anthropic`, so the app runs even before that package / an API key is
in place.

All current models are reasoning ("thinking") models — the `gpt-5.6` family
(`sol` / `terra` / `luna`) plus `gpt-5.4-nano`, `grok-4.5` / `grok-4.3`, and the
Claude 4.x/5 family. Two consequences live in `config.py`:

- **Token limits cover reasoning + output.** Reasoning is billed against
  `max_tokens`, so the per-feature limits are much larger than the old
  non-reasoning values; set them too low and a model returns an empty response.
- **`*_REASONING_EFFORT`** sets how hard OpenAI and Anthropic models think per
  feature (low for quick lookups and combat math, medium for planning). xAI and
  Gemini reason on their own and ignore it.

Effort is not uniform across Anthropic's models, so it is gated rather than sent
blindly. `ANTHROPIC_EFFORT_MODELS` lists the Claude ids that accept `effort`;
the API rejects it on models that predate the parameter (Haiku 4.5), so the
client drops it for anything not on that list. When adding a Claude model, check
the provider's effort docs and update that set — leaving a capable model off
only costs default behavior, but adding an incapable one turns every request for
it into a 400.

### Structured output

Rules / strategy / move use `chat.with_structured_output(Schema)` and return a
validated Pydantic object. If a model can't produce structured output, the
service logs a warning and falls back to a plain-text call wrapped in the same
schema, so the feature still works. Views return both a backward-compatible
display string and the structured object (`structured`) for the upcoming React
UI. The battle calculator returns its rigid fixed-format text unchanged.

### Error handling

Provider failures are classified into `AIServiceError` subclasses, each with a
clear `user_message` and `http_status`: missing key (400), invalid key (401),
input validation (400), rate limit (429), timeout (504), malformed response
(502), generic provider error (502). Views translate these via
`_ai_error_response`. Validation failures and structured-output fallbacks are
logged under the `core` logger.
