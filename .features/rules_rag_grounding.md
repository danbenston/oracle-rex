# Epic: Grounded Rules Q&A (RAG + citations + eval gate)

> **Scope.** Upgrade the existing Rules Q&A feature — same endpoint, same job
> flow, same panel — so answers are grounded in retrieved Living Rules Reference
> text with tappable citations, instead of relying on model recall. Adds a
> retrieval corpus + index, a retrieval step in front of the existing single
> model call, citation rendering in `RulesPanel`, and a two-tier eval layer
> (free deterministic retrieval evals in CI; opt-in promptfoo-style answer
> evals). **This is an enhancement of the rules feature, not a new feature** —
> no new tab, no new job type, no changes to strategy/move/calculator.
>
> **Branch:** suggested `epic/rules-rag-grounding` off `main`.

**Status: planned (2026-07-01).** Activates the grounded-citations scope that
`done/milestone_10_game_features.md` §1 explicitly deferred ("Living Rules
Reference (LRR) PDF — authoritative rules text... Only needed if the rules
chatbot later wants grounded citations").

---

## 1. Why

The rules chatbot currently answers from model recall with a one-shot example
(`rules_chat_v2`). Consequences:

- `RulesAnswer.rule_basis` is a list of strings the **model invents** — it looks
  like a citation but has no provenance. `needs_exact_text` exists precisely
  because the model can't see the rules text.
- Faction/timing edge cases (the questions people actually ask a rules bot) are
  exactly where LLM recall hallucinates.
- The small free-tier Gemini model is the default live path; small models are
  *bad* at recall but *good* at summarizing evidence placed in front of them.
  Retrieval converts our weakest configuration into a strong one.

RAG here means: deterministic retrieval over an indexed rules corpus → the same
single structured-output call, now with the relevant passages in the prompt →
citations that point at real rule numbers whose exact text the UI can show.

## 2. Source of truth & corpus

- **Corpus: the Living Rules Reference (LRR) PDF** — the authoritative rules
  text, as already noted in M10 §1. Covers base + Prophecy of Kings (+ Codex
  errata). **Phase 0 open item:** pin the exact LRR version and enumerate which
  Codex errata documents are folded in; record version + source URL as
  provenance in the vendored data.
- The LRR's internal structure is a gift: alphabetized numbered topics with
  numbered sub-rules (e.g. `58.4`), plus "Related Topics" links and a glossary.
  **Chunk by rule number, not by token window.** A chunk = one numbered
  sub-rule (with its topic heading for context); a topic intro is its own
  chunk. Citations are then naturally `LRR 58.4` — stable, human-checkable,
  and exactly the granularity players cite on forums.
- **Discordant Stars is out of corpus** (homebrew; not in the LRR). The prompt
  already scopes answers to base+PoK+DS; DS-specific questions keep falling back
  to model recall and should say so (see §4 prompt contract). A DS corpus from
  AsyncTI4 mechanical data is a possible later tier, not this epic.
- **IP hygiene** (mirror M10): rules text is Asmodee/FFG IP. Vendoring the
  parsed text server-side and quoting cited passages in answers is normal
  fair-use-shaped behavior for a free fan tool, but don't ship a "browse the
  whole rulebook" UI, and attribute the LRR (version + publisher) wherever
  citations render.

## 3. Storage & retrieval design

Constraints that drive this: Render free tier (single web service, ephemeral
disk), SQLite default with optional Postgres via `DATABASE_URL`, zero
per-request cost required on the no-key path, and the corpus is small (~1–3k
chunks, well under a few MB).

- **Vendored corpus:** `core/data/source/lrr/lrr_rules.json` — the parsed,
  normalized rule chunks (`rule_id`, `topic`, `text`, `parent_topic`,
  `related`, `source_version`), produced by the Phase 0 ingestion script and
  **committed** (the PDF itself is not committed). Validators wired into the
  existing `manage.py validate_data` flow: every `related` reference resolves;
  rule_id format/uniqueness; no empty texts.
- **Index: SQLite FTS5 (BM25), built from the vendored JSON.** FTS5 ships in
  Python's `sqlite3` — zero new dependencies, zero cost, deterministic, and
  fast at this corpus size. Build via a management command
  (`manage.py build_rules_index`) into a **standalone SQLite file** (e.g.
  `core/data/index/lrr_fts.sqlite3`), *separate from the app DB*. That
  sidesteps the SQLite-vs-Postgres split entirely (the corpus is read-only
  reference data, not app state) and survives Render's ephemeral disk by
  rebuilding in the build command (fast) — same pattern as `collectstatic`.
- **Query layer** (`core/service/rules_index/` — deterministic, no LLM):
  - `retrieve(question, k) -> list[RetrievedRule(rule_id, topic, text, score)]`
  - A small curated **alias/jargon map** applied at query time ("PDS" →
    "space cannon / structures", "taccy" → "tactical action", "cap" →
    "capacity", faction shorthands). TI jargon is the main lexical-retrieval
    failure mode; this is cheap and testable.
  - Include the glossary and topic intros in the index — short questions often
    match a topic name, and the topic intro chunk anchors the right
    neighborhood.
- **Embeddings are explicitly Phase 5, and only if evals demand it.** The
  fallback design (server-Gemini embedding API on the free key, vectors as
  blobs, brute-force numpy cosine — `numpy` is already a dependency) is cheap,
  but don't build it until the retrieval eval shows lexical+alias retrieval
  actually missing. This is the codegraft discipline: validate the signal
  before shipping it.

## 4. Pipeline integration (the actual feature change)

All inside the existing call path — `rules_job_create` → `run_ai_job` →
`get_rules_response`:

- **`get_rules_response`** gains a retrieval step: `retrieve(question, k)`
  (k≈6–10, tuned by the eval) before building messages. Retrieval results are
  threaded into the prompt *and* stamped into the job for observability.
- **Prompt `rules_chat_v3`** (`build_messages(question, passages)`):
  - passages rendered as a clearly delimited rules-reference block, each tagged
    with its `rule_id`;
  - instruction contract: answer **from the provided passages**; cite rule
    numbers actually used; if the passages don't cover the question (e.g.
    Discordant Stars content), say so explicitly and answer from general
    knowledge with a lowered-confidence flag rather than fake a citation.
  - Keep the existing one-shot example (updated to demonstrate citing).
  - Personas still apply via `personas.apply_persona` — unchanged.
- **Schema:** extend `RulesAnswer` (additive, backward compatible):
  - `citations: list[RuleCitation]` where `RuleCitation = {rule_id, relevance}`
    — the *quoted text comes from our index, not the model*, killing fabricated
    quotes by construction. Validate post-hoc: any cited `rule_id` not in the
    retrieved set gets dropped with a logged warning (anti-hallucination
    check, cheap and deterministic).
  - `grounded: bool` — whether the answer is passage-backed or recall-based
    (the DS/out-of-corpus path). `needs_exact_text` becomes largely vestigial
    but stays for schema compat.
  - `to_display_text()` renders citations as `LRR 58.4 — Movement` lines so the
    plain-text path stays useful.
- **Job result payload** additionally carries the retrieved passages
  (`rule_id`, `topic`, `text`, `score`) so the frontend can render exact text
  without a second endpoint, and `PROMPT_VERSION` bumps to `rules_chat_v3`
  (already stamped per job — free observability).
- **Feature flag:** `RULES_RAG_ENABLED` (settings/env, default on once shipped)
  so the pre-RAG path remains one env var away during rollout.
- **Token budget note:** passages add ~1–2k input tokens per question. Fine for
  BYOK and the Gemini free tier (input is the cheap direction); `k` is the
  knob if the live-demo cap ever pinches.

## 5. Frontend (`frontend/src/features/rulesChat/`)

- `RulesPanel` renders citations as chips/rows under the answer
  (`LRR 58.4 — Movement`); tapping one expands the exact rule text (already in
  the job payload — no extra fetch). Small LRR-version attribution line.
- Un-grounded answers (`grounded: false`) get a visible "answered from general
  knowledge — no rules text matched" treatment instead of silently looking
  authoritative.
- Types in `types/ai.ts`, rendering tests alongside `RulesPanel.test.tsx`.
- **Demo mode:** regenerate the cached rules demo response(s) in
  `core/demo/responses/` through the new pipeline so demo users see the
  citations UX (checklist item, easy to forget).

## 6. Eval layer (the promptfoo-style gate)

Two tiers, costed differently — the same free-vs-paid split the rest of the app
already practices:

- **Tier A — retrieval evals: deterministic, free, in CI (pytest).**
  A golden set (`core/data/eval/rules_golden.json`) of questions → expected
  `rule_id`s. Metrics: recall@k and MRR, exactly the codegraft-eval pattern.
  Seed ~25–40 cases from: the demo prompt chips, `sample_rules_questions.json`,
  and community classics (retreat timing, capacity vs. fighters at end of
  combat, space cannon offense timing, Nebula defense, sustain damage order,
  transactions timing...). Runs on every change to chunking/aliases/retrieval;
  **a chunking or alias change that drops recall fails CI.**
  **Baseline (Phase 1, LRR 2.0, 32 cases):** recall@3 = 0.844 · recall@5 = 0.969
  · recall@8 = 1.000 · recall@10 = 1.000 · MRR@10 = 0.700. Implemented in
  `core/tests/test_rules_retrieval.py`.
- **Tier B — answer evals: opt-in, costs tokens, promptfoo.**
  A `promptfoo` config (Node tooling already exists in `frontend/`) hitting the
  real pipeline with the golden questions; assertions per case: cited rule_ids
  include the expected ones, key-fact contains/not-contains checks, optional
  LLM-rubric grade on the server Gemini key. Run manually before/after prompt
  or model changes (`npm run eval:rules` or similar), never per-commit. This is
  the gate for `rules_chat_vN` bumps.
- **Flywheel:** when a live question produces a bad answer, the fix starts by
  adding it to the golden set (Tier A if retrieval missed; Tier B if the answer
  misused good passages).

## 7. Phases (each independently shippable)

- **Phase 0 — corpus ingestion. DONE 2026-07-03.** Corpus = the **official FFG
  PoK Living Rules Reference 2.0** (`ti10_pok_living_rules_reference_20_web.pdf`,
  base + PoK; URL + version stamped as provenance). `scripts/ingest_lrr.py`
  (pdfplumber, dev-only — NOT in requirements.txt) parses PDF → chunks:
  splits the two-column layout at page center, drops the tiny-font (~1.2pt)
  quick-reference diagrams via a `size>=5` filter, detects topic headings +
  `N.M` sub-rule markers by their hanging left-gutter position, folds lettered
  sub-clauses into their sub-rule, and extracts wrapped `RELATED TOPICS`. Output
  `core/data/source/lrr/lrr_rules.json` (committed; PDF gitignored) =
  **601 chunks: 101 topics (1..101) + 500 sub-rules**, with provenance header.
  Validator `validate_lrr_corpus()` wired into `manage.py validate_data`
  (unique/valid rule_ids, non-empty text, sub-rule→topic + related refs resolve)
  + 9 tests; full suite 145 green. *Known v1 limits (documented, faithful to the
  PDF, all TEXT retained):* the LRR itself skips some numbers (e.g. no 58.1) and
  renders ~7 sub-rules next to ALL-CAPS sub-headers where the FFG typesetting
  yields an off-by-one/dropped citation number across 4 topics; retrieval keys on
  text so this is acceptable. Codex coverage past LRR 2.0 is an open provenance
  item. *Exit met:* 101 topics == the RR's topic count; validators green.
- **Phase 1 — retrieval engine + eval harness. DONE 2026-07-03.** FTS5 index +
  query layer + golden set + retrieval eval, no app-behavior change. What landed:
  - `core/service/rules_index/` (deterministic, no LLM): `build_index()` →
    standalone `core/data/index/lrr_fts.sqlite3` (FTS5, porter stemmer,
    `topic`/`text` indexed, rest UNINDEXED; index gitignored, rebuilt from the
    committed corpus); `retrieve(question, k=8) -> [RetrievedRule]` with BM25
    ranking (topic weighted 10× over text); `aliases.py` TI-jargon map applied at
    query time (PDS→space cannon, taccy→tactical action, cap→capacity, ...).
  - `manage.py build_rules_index` + `manage.py rules_search "..."` (debug CLI).
  - Golden set `core/data/eval/rules_golden.json` — **32 cases** (demo chips +
    community classics), each mapped to verified answering `rule_id`s.
  - Retrieval eval `core/tests/test_rules_retrieval.py` (10 tests, `SimpleTestCase`
    so no app DB; builds a temp index): recall@k / MRR floors as a CI regression
    gate + golden-integrity + alias + determinism checks. Full backend suite 155
    green.
  - **Baseline (LRR 2.0, 32 cases, recorded here):** recall@3 = 0.844 ·
    recall@5 = 0.969 · **recall@8 = 1.000** · recall@10 = 1.000 · MRR@10 = 0.700.
    Every golden question's answer lands within the top 8 (inside the k≈6–10
    prompt budget). Floors in the test sit just below these.
  - *Exit met:* eval green with committed baseline; retrieval is deterministic
    and free; no pipeline/app change yet (that's Phase 2). Embeddings (Phase 5)
    are not justified — lexical + aliases already hit recall@8 = 1.0.
- **Phase 2 — pipeline integration. DONE 2026-07-03 (live check pending).**
  What landed:
  - `RulesAnswer` extended (additive): `citations: list[RuleCitation]`
    (`{rule_id, relevance}`) + `grounded: bool`; `to_display_text()` renders
    `LRR 58.4 — <relevance>` lines; `fallback_from_text()` sets grounded=False.
  - `rules_chat_v3` (`build_messages(question, passages)`): renders a delimited,
    rule-numbered RULES REFERENCE block + an answer-from-passages / cite-real-
    numbers / out-of-corpus→grounded=false contract; keeps the pre-RAG
    (`rules_chat_v2`) form when no passages, so `RULES_RAG_ENABLED=0` is a
    one-flag rollback. Personas unchanged.
  - Service: new `get_rules_result()` retrieves k=`RULES_RETRIEVAL_K` (8)
    passages, builds v3 messages, and runs post-hoc citation validation — any
    cited `rule_id` not in the retrieved set is dropped with a logged warning,
    and `grounded` is derived from the surviving citations (not the model's
    self-report). Retrieval failure (e.g. index not built) degrades gracefully to
    ungrounded recall. `get_rules_response()` kept as a thin answer-only wrapper.
  - `core/jobs.py` threads the retrieved `passages` into the rules job payload;
    `PROMPT_VERSIONS["rules"]` → `rules_chat_v3` (flag-aware stamp).
  - `RULES_RAG_ENABLED` (default on) + `RULES_RETRIEVAL_K` in config; deploy
    builds the index (`build_rules_index` added to render.yaml buildCommand).
  - Tests `core/tests/test_rules_rag.py` (12): grounded, citation-drop-with-
    warning, out-of-corpus ungrounded, grounded-override, fallback, flag-off,
    missing-index degrade, real-index integration, job-payload passages, backward
    compat. Full backend suite **167 green**.
  - **PENDING (owner, needs keys):** one live check per provider path (Gemini
    server key + one BYOK) that real citations come back. Verified offline that
    the grounded prompt renders correctly with 8 real passages.
  - *Exit:* flag on locally, citations validated deterministically; live check
    outstanding.
- **Phase 3 — frontend citations UX.** RulesPanel citations + expand +
  grounded/ungrounded states; demo cache regenerated; vitest + build green.
  *Exit:* deployed behind the flag; flag on in prod.
- **Phase 4 — answer evals + docs.** promptfoo config + runbook; README section
  ("grounded answers with LRR citations" is a headline feature — screenshot);
  record Tier B baseline. *Exit:* documented gate used for one real prompt
  tweak.
- **Phase 5 (conditional) — hybrid embeddings.** Only if Tier A shows lexical
  misses that aliases can't fix. Server-Gemini embeddings, numpy cosine, rerank
  BM25 top-50; adopt only on a clear eval delta (ablation discipline).

## 8. Non-goals

- No vector database, no external search service, no new runtime dependencies
  for retrieval (FTS5 is stdlib-reachable).
- No Discordant Stars rules text in this epic (flagged fallback instead).
- No multi-turn retrieval memory / conversation rewriting; retrieval is
  per-question.
- No changes to strategy/move/calculator features or their prompts.
- No "browse the rulebook" UI.
- No scraping pipelines — the LRR is ingested once from a pinned document by a
  script that's re-run manually when the LRR revs.

## 9. Definition of done

- Rules answers cite real LRR rule numbers; tapping a citation shows the exact
  text; quoted text is index-sourced, never model-generated.
- Out-of-corpus questions visibly say they're un-grounded.
- Retrieval evals run in CI with committed baselines; answer evals documented
  and runnable on demand; both used at least once to gate a real change.
- Demo mode shows the citation UX with zero provider calls.
- Feature works on the free path end-to-end: Gemini server key + FTS5
  retrieval = live grounded answers at $0.
- README updated with the grounding story and a citations screenshot.
