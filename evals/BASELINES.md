# Eval baselines

The committed, human-readable record of eval runs. Read the **delta** between
runs, not the absolute score. Update the relevant row whenever a prompt version,
model, or the corpus changes. Promptfoo's own HTML/JSON output stays uncommitted
(`evals/output/`); this file is the durable summary.

## Tier A — retrieval eval (deterministic, free, in CI)

`core/tests/test_rules_retrieval.py` over the golden set
(`core/data/eval/rules_golden.json`). No provider call.

| Date | Corpus | Cases | recall@3 | recall@5 | recall@8 | recall@10 | MRR@10 | Notes |
|------|--------|-------|----------|----------|----------|-----------|--------|-------|
| 2026-07-03 | LRR 2.0 | 32 | 0.844 | 0.969 | 1.000 | 1.000 | 0.700 | Phase 1 baseline |
| 2026-07-03 | LRR 2.0 | 34 | 0.853 | 0.971 | 1.000 | 1.000 | 0.718 | +transport pick-up/drop-off; "drop off" jargon alias added |

## Tier B — rules answer eval (promptfoo, opt-in, costs tokens)

`evals/promptfooconfig.rules.yaml` — the real pipeline over the same golden
questions. Asserts per case: structural (`pipeline_schema`) + grounded/cited
(`cited_rules`, i.e. the answer cites ≥1 expected LRR rule_id). Default provider
is the free-tier Gemini model.

**Run it** (from the repo root, with the index built and keys in the env):

```powershell
python manage.py build_rules_index                 # grounding needs the index
$env:PROMPTFOO_PYTHON = ".\.venv\Scripts\python.exe"
npx promptfoo@0.118.0 eval -c evals/promptfooconfig.rules.yaml
```

| Date | Model | Prompt | Cases | pipeline_schema pass | cited_rules pass | Notable failures |
|------|-------|--------|-------|----------------------|------------------|------------------|
| _pending_ | gemini-3.1-flash-lite | rules_chat_v3 | 34 | — | — | first run not yet recorded (needs a live GEMINI_API_KEY) |

> First real run is an owner step (it spends tokens). Fill the row above with the
> pass counts, then this becomes the gate for the next `rules_chat_vN` bump.
