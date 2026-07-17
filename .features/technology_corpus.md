# Epic: Technology corpus (ingest now, ground Rules Q&A now, feed calc/strategy later)

> **Scope.** Ingest every Twilight Imperium technology into a vendored,
> structured corpus that serves three consumers, and wire up the one that is
> cheap today.
>
> * **Now — Rules Q&A grounding.** Technology card text becomes retrievable, so
>   "what does Gravity Drive do?" is answered from the real card text with a
>   citation instead of model recall.
> * **Later — Tactical Calculator.** Unit-upgrade stats (Carrier II et al.)
>   replace today's base-stats-only simulation.
> * **Later — Strategy features.** Tech colors, prerequisites, and faction/set
>   membership enable tech-path reasoning.
>
> The two "later" consumers are **not built here**, but every field they need is
> captured now so the corpus is ingested once. No new tab, no new job type.
>
> **Branch:** `epic/technology-corpus` off `main`.
> **Status: Phases 0–1 DONE (2026-07-16). Phases 2–6 remain.**
>
> Landed: `scripts/ingest_tech.py` (5 page-family parsers + assembler +
> validation), the vendored corpus `core/data/source/tech/ti_technologies.json`
> (156 techs: base 60, pok 21, keleres 2, codex 4, discordant_stars 69; 43 unit
> upgrades), 11 committed wikitext fixtures under
> `core/tests/fixtures/tech_wiki/`, and 23 offline tests in
> `core/tests/test_tech_ingest.py`. The corpus regenerates deterministically from
> the fixtures. **Not yet started:** index integration (Phase 3), Rules Q&A
> wiring (Phase 4), the LRR chunk-101 fix (Phase 5), evals (Phase 6). The corpus
> exists but nothing reads it yet — it is not in the retrieval index.

---

## 1. Why

`core/data/source/lrr/lrr_rules.json` (601 chunks) contains the rules *about*
technology — topic 90 "Technology" (27 chunks) and topic 97 "Unit Upgrades" —
but **no technology card text**. Verified: `Gravity Drive`, `Antimass Deflector`,
and `Fighter II` appear nowhere in the corpus. Consequences:

- **Rules Q&A.** Any card-specific tech question falls to the ungrounded path and
  is answered from model recall, with no citation. This is the single largest
  known hole in the RAG corpus and the reason to do this now.
- **Tactical Calculator.** `core/service/combat/units.py` says it plainly: "the
  app tracks no unit-upgrade techs, so the simulator uses base stats (e.g.
  Cruiser II's 6 / Fighter II's 8 are not reflected)". The UI repeats it: "Base
  unit stats; unit-upgrade techs are not modeled." Fixing that needs upgrade
  stats, which is data, not logic.
- **Strategy.** Tech-path advice needs prerequisites and colors.

One ingest serves all three. The alternative — ingesting tech text now and unit
stats again later — parses the same pages twice.

## 2. Source and provenance

**Source: the TI4 Fandom wiki, via the MediaWiki API** (decided 2026-07-15).
The LRR pattern (parse an official PDF the owner supplies locally) does not
transfer: FFG publishes no document containing tech card text.

Use `https://twilight-imperium.fandom.com/api.php?action=parse&page=<P>&prop=wikitext`.
Verified reachable and returns clean wikitext. **Parse wikitext, not scraped
HTML** — the wikitext is stable, structured, and free of presentation noise.
(`WebFetch` on the rendered page returns HTTP 402; the API returns 200. Use the
API.)

The wiki is already this repo's precedent for reference data:
`milty_draft_factions.json` links every faction to its fandom page.

**Provenance header** mirrors `lrr_rules.json`'s, and must carry *both* layers:

```json
{
  "provenance": {
    "source": "Twilight Imperium 4E technology cards, transcribed via the TI4 Fandom wiki",
    "source_url": "https://twilight-imperium.fandom.com/wiki/Technology",
    "retrieved": "<ISO date>",
    "pages": ["Biotic Technologies", "..."],
    "wiki_license": "Fandom content is CC-BY-SA 3.0; attribution retained here.",
    "ip_note": "Mechanical card text is Fantasy Flight Games / Asmodee IP, vendored for a free fan tool (RAG grounding / citations), same basis as the LRR corpus.",
    "unofficial_note": "Discordant Stars is a fan-made expansion, not FFG-published; DS entries carry set='discordant_stars'."
  },
  "technologies": [ ... ]
}
```

Pin the retrieval date and the exact page list. A re-ingest is a deliberate,
by-hand act (like `ingest_lrr.py`), never a runtime fetch: **nothing in the
request path may ever call the wiki.**

## 3. Pages to ingest

From `Category:Technologies` (14 members, enumerated via the API):

**In scope**
| Page | Yields |
|---|---|
| `Biotic Technologies` | green techs |
| `Cybernetic Technologies` | yellow techs |
| `Propulsion Technologies` | blue techs |
| `Warfare Technologies` | red techs |
| `Unit Upgrade Technologies` | unit upgrades **+ stat tables** |
| `Faction Technologies` | base/PoK faction techs |
| `Discordant Stars Faction Technologies (UNOFFICIAL)` | DS faction techs |
| `Discordant Stars Starting Technology (UNOFFICIAL)` | DS starting tech |
| `Discordant Stars Faction Specific Units (UNOFFICIAL)` | DS unit upgrades |
| `Starting Technology` | per-faction starting sets |
| `Exhaustible Technologies` | cross-cutting flag |

**Explicitly excluded — this is a real trap.** `Technology (First Edition)`,
`Technology (Second Edition)`, `Technology (Third Edition)` are also in the
category. Earlier editions have different names and mechanics; ingesting them
would poison the corpus with authoritative-looking wrong-edition text. The
ingest script must take an **explicit page allowlist**, never "walk the
category", and must fail loudly on an unrecognized page.

## 3a. Phase 0 survey findings (2026-07-15) — corrections to this plan

Surveyed all 11 pages' wikitext. Five findings that change §4/§5:

1. **Thunder's Edge exists and is in scope-adjacent data.** TE is an official FFG
   expansion (released 2025-10-24), a third expansion after PoK. Its techs are
   interleaved into the same colour pages. **Decision: exclude for now** — the
   app models no TE factions (`milty_draft_factions.json`) and the LRR corpus is
   PoK-era, so grounding TE tech against PoK rules would be inconsistent. Revisit
   as a later phase of this epic.
2. **`{{Edition|...}}` is not a reliable marker.** It is absent from
   `Unit Upgrade Technologies` and every DS page, and tags only ~14 of ~30 techs
   on colour pages. **Section headings are the primary set discriminator; the
   template is a per-card override where present.**
3. **Codex I / III / IV techs exist** and are official. Council Keleres (Codex
   III) is already a faction the app models, so Codex content is in scope.
4. **Unit upgrades have no edition marker at all**, yet contain TE content
   (`4x41C "Helios" V2 ([[Last Bastion]])`). The only discriminator is the
   faction. **Parse `Faction Technologies` first to build a faction -> set map,
   then filter unit upgrades through it.**
5. **Faction techs are duplicated** across the colour pages (as `h3` faction
   sections with `h4` techs) and `Faction Technologies`. Take them from
   `Faction Technologies` only (canonical: explicit edition sections, and each
   entry names its own colour); colour-page parsing must skip any `h3` that is a
   faction wikilink.

**`set` vocabulary** reuses the app's existing convention from
`milty_draft_factions.json` (`base`, `pok`, `keleres`, `discordant`,
`discordantexp`) rather than inventing one, plus `codex_i` / `codex_iv` for
non-faction Codex techs. This makes `faction` cross-referenceable against the
app's faction ids — which doubles as the strongest available validation: **any
faction that fails to resolve to a known id is a TE leak or a typo.**

Excluded page sections: `FAQ` (colour pages), `Gallery` (unit upgrades).

## 4. The parsing risk (read this before estimating)

**There is no single wiki format. Each page family needs its own parser.** Five
confirmed shapes (the survey found two more than this section originally listed;
`Faction Technologies` and `Starting Technology` are their own formats):

1. **Color pages** (`Biotic Technologies`): `=== Tech Name ===` heading, then an
   `article-table` whose "Req." rows hold prerequisites, with card text as a
   bullet list.
2. **`Unit Upgrade Technologies`**: `==[[Carrier]]==` heading, then a stats table
   whose columns are `Cost | Combat | Move | Capacity` — **column sets vary per
   unit** (Destroyer II adds AFB, Dreadnought II adds Sustain/Bombardment). The
   labels live in a trailing row, so the parser must **read the label row and map
   by name**, never assume column positions.
3. **DS pages**: no tables at all. Inline
   `{{Tech|propulsion}} '''<big>Rift Engines</big>'''<blockquote>text</blockquote><blockquote>Prerequisites: {{Tech|propulsion}}</blockquote>`.

**The prerequisite gotcha.** Prereqs are `{{Tech|<color>}}` template calls, but a
tech's *own* colour badge is also a `{{Tech|<color>|w=32px}}` call in the same
table. Counting `{{Tech|...}}` naively gives every tech one phantom prerequisite.
Rule: **a bare `{{Tech|color}}` is a prereq; one with a `w=` argument is the
card's own badge.** Verified against Neural Motivator (0 prereqs), Dacxive
Animators (1 green), Hyper Metabolism (2 green).

Budget the parser work accordingly, and **survey each page family before writing
its parser** rather than generalising from the first one.

## 5. Corpus schema (dual-purpose)

One record per tech, carrying *both* structured fields (for the calculator and
strategy) and prose (for RAG). `core/data/source/tech/ti_technologies.json`:

```jsonc
{
  "id": "gravity_drive",
  "name": "Gravity Drive",
  "color": "propulsion",           // biotic|cybernetic|propulsion|warfare|unit_upgrade|faction
  "prerequisites": ["propulsion"], // list of colors; [] for starting techs
  "set": "base",                   // base|pok|discordant_stars
  "faction": null,                 // faction id when faction-specific
  "exhaustible": false,
  "text": "After you activate a system, apply +1 to the move value of 1 of your ships during this tactical action.",
  "starting_for": ["sol", "..."],  // factions starting with it
  "unit_upgrade": null              // populated ONLY for unit upgrades:
  // {
  //   "unit": "carrier", "cost": 3, "combat": 9, "move": 2, "capacity": 6,
  //   "sustain": false, "afb": null, "bombardment": null, "space_cannon": null
  // }
}
```

`unit_upgrade` field names deliberately mirror `UnitStats` in
`core/service/combat/units.py` (`combat`, `dice`, `sustain`, `afb`,
`bombardment`, `space_cannon`, `cost`) so the future calculator work is a
lookup-and-override, not a translation layer. Wiki tables also give `move` and
`capacity`, which `UnitStats` lacks — capture them anyway; they are free here and
the calculator may grow into them.

## 6. Retrieval design: one index, two sources

Add tech chunks to the **existing FTS index**, do not build a second one.

Rationale: BM25 scores are not comparable across indexes (different IDF corpora),
so two indexes cannot be merged into one ranked list without inventing a fusion
rule. One index gives correct unified ranking and leaves `retrieve()` unchanged.

- Keep the two **source** JSONs separate and independently versioned
  (`lrr/lrr_rules.json`, `tech/ti_technologies.json`).
- `builder.build_index` grows to accept a **list** of sources; `index_meta`
  records a version per source.
- Tech becomes `kind="technology"` alongside the existing `topic_intro` / `rule`.
- The index file name (`lrr_fts.sqlite3`) becomes a misnomer once it holds more
  than the LRR. Rename to something neutral and update `INDEX_PATH`,
  `build_rules_index`, `rules_search`, and the deploy step.

**Chunk mapping** (`RetrievedRule` fields, unchanged shape):
- `rule_id` = the tech **name** ("Gravity Drive"). Set-membership citation
  validation in `_validate_citations` is id-based and generic, so this works with
  no change. A name can never collide with an `N.M` rule id.
- `topic` = the tech name too (topic is weighted 10x in `bm25()`, which is what
  makes "what does Gravity Drive do?" land on the right chunk).
- `text` = card text **plus** a rendered stat/prereq line, so the numbers are
  retrievable as words, not just structured fields.

**Known non-goal:** `_RULE_ID_IN_TEXT` (`\b(\d+\.\d+)\b`) recovers inline rule
numbers from prose. It will not recover inline tech names, so a model that writes
"Gravity Drive lets you..." without filling `citations` won't get a harvested
cite. That is a missed bonus, not a regression. Revisit only if evals show it
matters.

## 7. Prompt and UI

- `prompts/rules_chat.py` (`_SYSTEM_GROUNDED`) instructs the model to cite "rule
  numbers (rule_id)". Extend so a tech name is a valid citation, and render tech
  passages distinguishably in the RULES REFERENCE block. **Bump to
  `rules_chat_v4`** and update `PROMPT_VERSIONS`.
- **The Discordant Stars copy needs care.** Both the prompt ("Discordant Stars
  faction content is not in the LRR") and `RuleCitations.tsx` ("this may be
  Discordant Stars or other out-of-reference content") assume DS is out of
  corpus. After this, DS **technology** is in corpus while DS faction abilities,
  leaders, and mechs still are not. Make the copy precise rather than deleting
  it — the ungrounded path remains correct for everything except tech.
- Check `RuleCitations.tsx` renders a name-style citation ("Gravity Drive")
  sensibly where it currently expects a rule number.

## 8. LRR chunk 101 fix (folded in, decided 2026-07-15)

The PDF's back-of-book index was appended to the last topic's chunk, so chunk 101
("Wormholes") ends with `"This index refers to paragraph numbers instead of page
numbers... ready planets, 8.4 commanders, 51.5..."`.

This is **not cosmetic and it is why it belongs in this effort**: those index
entries name technologies, so `Plasma Scoring` and `Dreadnought II` currently
match the *Wormholes* chunk. The moment tech questions become common, chunk 101
becomes a recurring false positive competing with the real tech chunk.

Fix in `scripts/ingest_lrr.py` (terminate parsing at the index heading) **and**
re-emit `lrr_rules.json`, so a future re-ingest stays clean. Guard with a test
asserting no chunk contains the index preamble.

## 9. Phases

- **Phase 0 — Survey.** Pull wikitext for one page per family; confirm the three
  shapes above and the `w=` prereq rule. Cheap, and it de-risks Phase 1.
- **Phase 1 — `scripts/ingest_tech.py`.** Allowlisted pages, per-family parsers,
  emits `core/data/source/tech/ti_technologies.json`. Dev-only dep, like
  `ingest_lrr.py`. Offline unit tests over committed wikitext fixtures — do not
  hit the network in tests.
- **Phase 2 — Validation.** Assert expected counts per color/set, every
  unit-upgrade maps to a known `units.py` key, no tech has a phantom prereq, no
  earlier-edition names leak in. This is the accuracy gate for a fan-sourced
  corpus; spot-check a sample against the physical cards.
- **Phase 3 — Index.** Multi-source `build_index`, rename the index file,
  `kind="technology"`, update the management commands.
- **Phase 4 — Rules Q&A.** Prompt v4, DS copy, citation rendering. **Ships now.**
- **Phase 5 — LRR chunk 101 fix.** Independent of 1–4; can land first.
- **Phase 6 — Evals.** Retrieval cases (tech name -> right chunk) in the free
  deterministic tier. Add tech Q&A cases to the golden set. **Regression guard:
  the existing LRR retrieval cases must not degrade** — adding ~100 chunks
  changes BM25 IDF for every query, so re-run `recall@8` (currently 1.0) and
  treat a drop as a blocker.

**Deferred (data captured, not built):** tactical calculator unit upgrades;
strategy tech-path reasoning.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Fan-sourced text is wrong or stale | Phase 2 validation + physical-card spot-check; provenance records the retrieval date |
| Earlier-edition pages leak in | Explicit allowlist; fail on unknown page |
| Phantom prereqs from `{{Tech}}` badges | The `w=` rule, asserted in tests |
| Wiki markup drifts and breaks re-ingest | Ingest is by-hand and versioned; fixtures pin the parsed shape |
| **Adding ~100 chunks degrades existing LRR retrieval** | Re-run retrieval evals; recall@8 must hold at 1.0 |
| DS-in-corpus makes the ungrounded copy wrong | §7 — make the copy precise, don't delete it |

See [[rules_rag_grounding]] for the pipeline this extends, and
[[rules_offtopic_guardrail]] for the related scope work.
