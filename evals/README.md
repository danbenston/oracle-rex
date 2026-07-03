# Oracle Rex answer-quality evals (promptfoo)

An **opt-in, token-costing** quality gate for prompt and model changes. It runs a
fixed set of cases through the **real** service pipeline (the same
`get_rules_response` / `get_strategy_response` / ... the app calls), asserts
deterministic facts about the outputs, and lets you compare runs — so a
`rules_chat_vN` bump, a persona tweak, a new Gemini model id, or a provider swap
is validated by evidence instead of one manual spot-check.

This **complements, never replaces** the pytest suite. pytest covers the
deterministic pipeline with mocked providers (free, in CI); promptfoo covers what
mocks can't — the actual model behavior behind each prompt version.

> **Status:** first cut. Rules Q&A is wired end to end. Strategy, move, and
> tac_calc dispatch already exists in the provider but their case files/configs
> are not built yet (see [Roadmap](#roadmap)).

## Ground rules (cost + trust)

- **Opt-in only, never per-commit CI.** Every run costs tokens. Run it manually
  before/after prompt-version bumps, model additions/retirements, and persona
  changes.
- **Default provider is the server-held Gemini free-tier model** — the same cost
  ceiling the live demo uses. Cross-provider matrix runs (BYOK keys) are a
  deliberate, occasional choice.
- **Deterministic assertions first.** Schema validity, prompt-version stamping,
  and (post-RAG) citation checks are free once the response exists. Model-graded
  rubrics (`llm-rubric`) are allowed but few, and graded by the cheap Gemini
  model.
- **Keys come from the environment only.** Nothing key-shaped goes in a config or
  a committed output.

## Prerequisites

- **Node** (already required by `frontend/`; this repo builds on Node
  `22.14.0`). promptfoo is run via `npx` — no new backend dependency, nothing
  added to `requirements.txt` or `frontend/package.json`.
- **The project virtualenv** at `.venv/` with the app installed — the provider
  does `django.setup()` and imports `core.service.ai`. promptfoo shells out to
  this Python (see [Python interpreter](#python-interpreter)).
- **Pinned promptfoo version:** `promptfoo@0.121.17`. Pin it on every invocation
  so runs are reproducible.

### Keys (environment)

| Env var | Needed for |
| --- | --- |
| `GEMINI_API_KEY` | The default Gemini provider (server-held key). Required for any run. |
| `GOOGLE_API_KEY` | The `llm-rubric` **grader** (promptfoo's Google provider reads this). Set it to the same value as `GEMINI_API_KEY`. Only needed if a config uses `llm-rubric`. |
| `OPENAI_API_KEY` / `XAI_API_KEY` / `ANTHROPIC_API_KEY` | Only for matrix runs against those providers (BYOK). |

promptfoo reads a `.env` in the working directory automatically. Put the keys
there or export them in the shell. **Do not commit `.env`.**

### Python interpreter

The provider must run under the project venv (so `core.service.ai` and Django are
importable). Point promptfoo at it with `PROMPTFOO_PYTHON`:

```powershell
# PowerShell (repo root)
$env:PROMPTFOO_PYTHON = ".\.venv\Scripts\python.exe"
```

```bash
# Git Bash
export PROMPTFOO_PYTHON="$PWD/.venv/Scripts/python.exe"
```

## Layout

```
evals/
  providers/oracle_rex_provider.py   # django.setup(); routes to core.service.ai
  asserts/pipeline_schema.py         # deterministic structural assertion (all features)
  cases/rules.yaml                   # rules test cases (starter set; see below)
  promptfooconfig.rules.yaml         # rules run config (Gemini default + matrix, commented)
  README.md                          # this runbook
  BASELINES.md                       # committed human summary per feature (added after 1st green run)
  output/                            # promptfoo reports — gitignored, never committed
```

## Run it

From the **repo root** (so `.venv` and `.env` resolve):

```powershell
# Rules Q&A, default Gemini provider
npx promptfoo@0.121.17 eval -c evals/promptfooconfig.rules.yaml

# Open the last run in the local web viewer
npx promptfoo@0.121.17 view
```

### Cheap iteration (smoke subset)

While editing a prompt, run only the cases tagged `smoke: true` (3 of them):

```powershell
npx promptfoo@0.121.17 eval -c evals/promptfooconfig.rules.yaml --filter-metadata smoke=true
# or just the first N cases:
npx promptfoo@0.121.17 eval -c evals/promptfooconfig.rules.yaml --filter-first-n 3
```

Run the **full** set before merging a prompt change.

### Matrix runs (deliberate, extra cost)

- **Providers/models:** uncomment the extra `providers` blocks in
  `promptfooconfig.rules.yaml` and set that provider's key in `.env`. This is the
  evidence step for adopting a new model id or retiring one — pair it with
  `scripts/check_model_availability.py` (the replacement model should pass the
  eval set, not just exist).
- **Personas:** set `persona:` in a provider's `config` (or per case in
  `cases/rules.yaml`) to run the set across `default` / `oracle` / `war_machine`
  and confirm facts survive tone changes.

### Saving a report

Outputs stay uncommitted. Write one when you want to diff a run:

```powershell
npx promptfoo@0.121.17 eval -c evals/promptfooconfig.rules.yaml -o evals/output/rules-$(Get-Date -Format yyyyMMdd).json
```

## What the assertions check

- **`pipeline_schema.py` (every case, via `defaultTest`)** — the response is
  valid JSON, the real Pydantic schema (`RulesAnswer`, etc.) validates, the
  answer field is non-empty, and the prompt version is stamped. Free. Set
  `allow_fallback: false` in a case's `vars` to also fail when the model could
  not produce native structured output and the service degraded to plain text.
- **Per-case `contains` / `not-contains`** — key-fact checks. These are
  intentionally light and heuristic (LLM prose varies); tune them against the
  first real run and read the **delta** between runs, not the absolute score.
- **`llm-rubric` (a few cases)** — model-graded, using the cheap Gemini model as
  grader.

### The `_meta` block

The provider returns the structured result plus a `_meta` object so assertions
can check **pipeline facts, not just text**:

```json
{ "answer": "...", "rule_basis": ["..."],
  "_meta": { "feature": "rules", "model": "gemini-3.1-flash-lite",
             "persona": "default", "prompt_version": "rules_chat_v1",
             "schema_valid": true, "fallback_used": false } }
```

## Cost expectations

- Rules cases are short prompts with small outputs on the cheap Gemini model. The
  10-case set is a handful of cents-scale-or-less on the free tier; the 3-case
  smoke subset is trivial. `llm-rubric` adds one extra cheap Gemini call per
  rubric'd case.
- **Nothing runs automatically or on someone else's dime.** No per-commit CI, no
  runs on user/demo traffic.

## Cases: starter vs. golden set

`cases/rules.yaml` is a **10-case starter**. The RAG epic
(`.features/rules_rag_grounding.md`) owns the single curated question list at
`core/data/eval/rules_golden.json`; when it lands, these cases are regenerated
from it so there is exactly one curated question source in the repo. The starter
has no citation assertions because `RulesAnswer` has no citations pre-RAG — the
RAG epic's Phase 4 adds cited-`rule_id` and grounded/ungrounded checks to this
same file.

## Baselines & regression discipline

After the first green run of a feature, record a short entry in `BASELINES.md`
(date, model, case count, pass counts, notable failures) and update it whenever a
prompt version bumps. The committed record of a run is that human summary —
promptfoo's own HTML/JSON outputs stay in `output/` and are gitignored.

## Roadmap

1. ✅ `evals/` skeleton + provider + rules cases + config + runbook.
2. Regenerate rules cases from `rules_golden.json` when the RAG epic lands.
3. `promptfooconfig.strategy.yaml` + fixtures + the vendored-tech python
   assertion (every tech in `tech_path` must exist in the AsyncTI4 data).
4. `promptfooconfig.move.yaml` + `promptfooconfig.tac_calc.yaml` (light).
5. Persona + provider matrix variants (documented above; provider blocks
   commented in the config).
6. First `BASELINES.md` entries; add "run the relevant eval config" to the
   prompt-change checklist in `core/service/ai/README.md`.
