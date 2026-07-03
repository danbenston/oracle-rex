# Feature: promptfoo eval harness (answer-quality gate for the AI features)

_Status: planned (2026-07-01). Companion to `rules_rag_grounding.md` — that
epic's Phase 4 ("Tier B answer evals") **is** this harness applied to the rules
feature; this doc owns the shared infrastructure and extends coverage to the
other AI features where evals earn their token cost. Either doc can land
first; whichever is second consumes the other's pieces._

## Objective

A repeatable, opt-in answer-quality gate for prompt and model changes: run a
fixed case set through the **real** service pipeline, assert deterministic
facts about the outputs, and compare runs — so a `rules_chat_v3` bump, a
persona tweak, a new Gemini model id, or a provider swap is validated by
evidence instead of one manual spot-check.

This complements, never replaces, the existing test suite: pytest covers the
deterministic pipeline with mocked providers; promptfoo covers what mocks
can't — the actual model behavior behind each prompt version.

## Ground rules (cost + trust)

- **Opt-in only, never per-commit CI.** Every promptfoo run costs tokens. It
  runs manually before/after prompt-version bumps, model additions/
  retirements, and persona changes. (Optional later: a `workflow_dispatch`
  GitHub Action gated on secret presence — explicitly not in the first cut.)
- **Default provider for eval runs: the server-held Gemini free tier** —
  the same cost ceiling the live demo uses. Cross-provider matrix runs (BYOK
  keys from `.env`) are a deliberate, occasional choice.
- **Deterministic assertions first.** Pydantic-schema validity, required-
  citation checks, real-name checks against vendored game data — these are
  free once the response exists. Model-graded rubrics (`llm-rubric`) are
  allowed but few, and the grader is the cheap Gemini model.
- **Keys come from `.env`/environment only** (promptfoo reads env natively);
  nothing key-shaped in configs or committed outputs.

## Architecture — call the real pipeline, not a copy of the prompt

Prompts here are assembled in code (`build_messages` per feature +
`personas.apply_persona` +, post-RAG, retrieval). Duplicating that text into
promptfoo YAML would drift immediately. So the harness uses **promptfoo's
custom Python provider** pointing at a thin adapter over the service layer:

```
evals/
  providers/oracle_rex_provider.py   # django.setup(); routes to core.service.ai
  cases/
    rules.yaml        # test cases (or generated — see rules note below)
    strategy.yaml
    move.yaml
  promptfooconfig.rules.yaml
  promptfooconfig.strategy.yaml
  promptfooconfig.move.yaml
  README.md           # runbook: env, commands, cost expectations
```

- `oracle_rex_provider.py` implements promptfoo's `call_api` contract:
  reads `{feature, model, persona, ...inputs}` from the test vars, calls the
  real `get_rules_response` / `get_strategy_response` / `get_move_response`,
  and returns the JSON-serialized structured object **plus metadata**
  (validated-schema pass/fail, `PROMPT_VERSION`, fallback-path-taken) so
  assertions can check pipeline facts, not just text. Uses the project venv
  and `django.setup()` — same pattern as `scripts/check_model_availability.py`.
- Run via `npx promptfoo@<pinned> eval -c evals/promptfooconfig.<feature>.yaml`
  (pin the version in the runbook; no new backend dependency). Windows-first,
  like everything else in this repo.
- One config per feature keeps runs small and targeted; a shared `providers`
  block lists the model matrix (Gemini default; others commented for matrix
  days).

## Where evals make sense (and where they don't)

### 1. Rules Q&A — the flagship (shared with the RAG epic)

Cases come from **the same golden set** the RAG epic defines
(`core/data/eval/rules_golden.json`) — a tiny converter (or promptfoo's
external-file test loading) turns golden entries into promptfoo cases, so
there is exactly one curated question list in the repo. Assertions:

- structured `RulesAnswer` validates (metadata flag from the provider);
- post-RAG: cited `rule_id`s include the expected ones; `grounded: true` for
  in-corpus questions, `grounded: false` honestly flagged for DS questions;
- key-fact `contains`/`not-contains` per case (e.g. retreat-timing answers
  must not claim you can retreat with no ships);
- optional `llm-rubric` ("answers the question asked; no invented rules") on
  a subset.

### 2. Strategy suggester — the hallucination hotspot with ground truth

M10 vendored the AsyncTI4 faction-tech data precisely because faction techs
are where models invent things. That data is now an **assertion oracle**:

- inputs: 3–5 fixture board states from `core/demo/scenarios/` × a spread of
  factions (include at least one Discordant Stars faction);
- `StrategicPlan` schema validity;
- **python assertion: every tech named in `tech_path` exists in the vendored
  dataset for that faction or the generic tree** — the single highest-value
  assertion in this whole harness (deterministic hallucination detection);
- sanity `contains`-style checks per fixture (e.g. the faction's signature
  ability referenced for ability-driven factions);
- optional rubric: "advice is specific to this board, not generic TI advice."

### 3. Move suggester — lighter tier

Same fixture approach, weaker oracles (moves are judgment calls):

- `TacticalMove` schema validity;
- python assertion: referenced tiles/units exist in the input board state
  (no phantom fleets);
- consistency spot-check where the fixture includes calculator odds (a
  recommended attack shouldn't cite a win chance that contradicts the
  provided simulation numbers).

### 4. Battle calculator narrative — minimal by design

The math is deterministic and already unit-tested; the LLM only narrates.
One small case file asserting the rigid output format parses and the narrated
numbers match the simulation inputs. No rubric. (Cheapest config; mostly a
canary that format instructions still hold on new models.)

### 5. Cross-cutting matrix dimensions (not separate features)

- **Personas**: run the rules + strategy cases across `persona ∈ {default,
  each shipped persona}` occasionally — assert facts/citations survive tone
  changes (persona modifies voice, never content).
- **Providers/models**: the same case sets across the model registry before
  adopting a new model id or retiring one — this gives the
  `api_model_retirement_monitor` flow an evidence step ("the replacement
  model passes the eval set") instead of swap-and-hope.

## Baselines & regression discipline

- After the first green run per feature, commit a short
  `evals/BASELINES.md`: date, model, case count, pass counts, notable
  failures. Update it whenever a prompt version bumps — the codegraft rule
  applies: **read the delta between runs, not the absolute score.**
- Promptfoo's own HTML/JSON outputs stay uncommitted (`evals/output/` in
  `.gitignore`); the committed baseline is the human summary.
- Smoke mode for cheap iteration: run a marked subset (`--filter-first-n` or
  a `smoke: true` tag) while editing prompts; full set before merging the
  prompt change.

## Non-goals

- No per-commit CI evals; no eval runs on user/demo traffic.
- No paid hosted eval platform; promptfoo runs locally.
- No retrieval-quality evals here — that's the RAG epic's Tier A (pytest,
  free, in CI). This harness starts where a provider call is required.
- No attempt to score strategy *quality* beyond specificity/validity — "is
  this good TI advice" is not reliably assertable and rubric-grading it
  deeply would burn tokens for noise.

## Implementation sequence

1. `evals/` skeleton + `oracle_rex_provider.py` (rules feature only, Gemini
   default) + runbook; prove one end-to-end `npx promptfoo eval` locally.
2. Rules cases wired from the golden set (or a 10-case starter if this lands
   before the RAG epic's golden file exists — converge on one file later).
3. Strategy config + fixtures + the vendored-tech python assertion.
4. Move + calculator configs (light).
5. Persona and provider matrix variants documented in the runbook (commented
   provider blocks, how/when to run them).
6. First `BASELINES.md` entries; add "run the relevant eval config" to the
   prompt-change checklist in `core/service/ai/README.md`.

## Acceptance criteria

- One command per feature runs the real pipeline against its case set on the
  free-tier key and reports pass/fail per assertion.
- The strategy eval deterministically flags an invented faction tech.
- Rules evals share their question source with the RAG golden set (single
  curated list).
- Baselines are committed; the AI-service README tells contributors when to
  run which config.
- Nothing runs automatically or on someone else's dime.
