# Enhancement: scope guardrail for Rules Q&A (decline off-topic questions)

> **Status: NOT STARTED — noted 2026-07-15.** Future enhancement, no branch yet.
> Scope: the Rules Q&A feature only (`rules_chat` prompt + `RulesAnswer` schema
> + `RuleCitations` note). No new tab, no new job type.

---

## 1. Why

Rules Q&A currently answers *any* question, not just Twilight Imperium ones.
Observed 2026-07-15: asked "How many jumping jacks per day is recommended for
good heart health?", Oracle Rex answered it — noting the rules reference didn't
cover it, then giving general cardiovascular-exercise advice, flagged with the
standard "Answered from general knowledge; no matching rules text was found
(this may be Discordant Stars or other out-of-reference content)" note.

That is working as designed, and the design is the problem. Three reasons to
fix it:

- **Wrong product.** A TI rules assistant that answers arbitrary questions reads
  as an unscoped chatbot wrapper rather than a domain tool.
- **The ungrounded note becomes misleading.** It tells the user the answer may be
  "Discordant Stars or other out-of-reference content". For a health question
  that framing is nonsense: it implies the question was in-domain but
  out-of-corpus, which it was not.
- **Liability.** The off-topic surface includes medical, legal, and financial
  questions. Answering those from a board-game assistant is a real risk, and it
  is the failure mode most likely to matter to a user who is not playing along.

## 2. The subtlety (do not just refuse when ungrounded)

The obvious fix — refuse whenever `grounded=false` — is **wrong** and would
regress a deliberate feature. `prompts/rules_chat.py` instructs the model, in
grounded mode, to answer from general knowledge with `grounded=false` when the
retrieved passages don't cover the question, *specifically so out-of-corpus TI
content still works*: the LRR corpus has no Discordant Stars faction text, and
answering those from recall is intended behavior, not a bug.

So the feature needs a **third state**, not a binary:

| Question | Retrieval | Desired behavior |
|---|---|---|
| In LRR corpus ("Can I retreat?") | hits | Answer grounded, cite rule ids. Unchanged. |
| On-topic, out of corpus (Discordant Stars faction) | no useful hits | Answer from general knowledge, `grounded=false`. **Unchanged — keep this.** |
| Off-topic (jumping jacks) | no useful hits | **New:** decline with a generic in-character message, no answer attempted. |

Rows 2 and 3 look identical to the current code: both are "no matching passages".
The retrieval score alone cannot separate them, so the classifier has to be about
*topic*, not *coverage*.

## 3. Sketch

Two candidate approaches, cheapest first:

1. **Prompt-only.** Extend `_SYSTEM_GROUNDED` with a scope clause plus a new
   `RulesAnswer` field (e.g. `off_topic: bool`), and have the model self-classify.
   Nearly free (no extra call), and the model already sees the question. Risk: it
   is the model policing its own scope, and the boundary is genuinely fuzzy
   (is "what does a d10 average?" on-topic? "who designed TI4?").
2. **Deterministic pre-check** before the provider call, refusing without
   spending a request. Cheaper per off-topic hit and un-jailbreakable, but a
   keyword/embedding gate on TI vocabulary will misfire on legitimately odd
   phrasing, and a false refusal is a worse failure than a stray answer.

Recommend starting with (1) and only adding (2) if evals show the model is a poor
scope judge. Either way the decline copy should be generic, in Oracle Rex's
voice, and must **not** reuse the "Discordant Stars or other out-of-reference
content" note — `RuleCitations.tsx` needs a distinct branch, since that copy is
what makes the current behavior actively confusing.

## 4. Eval

This is exactly what the promptfoo rules harness is for, and the cases are cheap
to write. Add an off-topic set to the golden set covering: a benign non-TI
question (jumping jacks), a medical/legal/financial one, an adjacent-but-not-TI
one (another board game), and a TI-flavored trick ("what's the real-world price
of the TI4 box?"). Assert a decline.

**Guard against over-refusal**, which is the likelier regression: the existing
Discordant Stars cases must keep returning `grounded=false` *answers*, not
refusals. Those cases already exist in the golden set and would catch it.

See [[rules_rag_grounding]] for the grounded path this builds on, and
`evals/README.md` for the harness.
