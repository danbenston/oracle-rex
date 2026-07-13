"""Prompt for the Rules Q&A chatbot.

Two modes, selected by whether retrieved passages are supplied:

* **Grounded (rules_chat_v3)** — the default once RAG is enabled. Relevant Living
  Rules Reference passages are rendered as a delimited, rule-numbered block and
  the model is told to answer *from those passages* and cite the rule numbers it
  used. If the passages don't cover the question (e.g. Discordant Stars content,
  which is out of corpus), it says so and answers from general knowledge with
  ``grounded=false`` instead of faking a citation.
* **Pre-RAG (rules_chat_v2)** — no passages supplied; the model answers from
  recall, as before. Kept so ``RULES_RAG_ENABLED=0`` is a one-flag rollback.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

PROMPT_VERSION = "rules_chat_v3"

_SYSTEM_BASE = (
    "You are Oracle Rex, a chatbot that answers rules questions about the board "
    "game Twilight Imperium. Answers should be based on the 4th edition of the "
    "game with the Prophecy of Kings and Discordant Stars expansions unless "
    "otherwise stated. Be accurate and concise."
)

# Appended in grounded mode: the answer-from-passages contract.
_SYSTEM_GROUNDED = (
    " You are given a RULES REFERENCE block containing numbered passages from the "
    "official Living Rules Reference (LRR). Answer from those passages. In "
    "'citations', list the rule numbers (rule_id) you actually relied on, taken "
    "verbatim from the block — never cite a rule number that is not in the block, "
    "and never invent rule text. Set 'grounded' to true when your answer is "
    "supported by the passages. If the passages do not cover the question (for "
    "example, Discordant Stars faction content is not in the LRR), say so plainly, "
    "answer from general knowledge, set 'grounded' to false, and leave 'citations' "
    "empty rather than citing an unrelated rule."
)

# Appended in pre-RAG mode.
_SYSTEM_RECALL = (
    " If a fully confident answer would require the exact card or rules text, say so."
)

# One-shot example (grounded): demonstrates citing a rule number from the block.
_EXAMPLE_Q = (
    "I want to move a carrier two tiles. Can I pick up or drop off ground forces "
    "while doing so? Can I do both in the same move?"
)
_EXAMPLE_A_GROUNDED = (
    "Ships with sufficient capacity can pick up ground units from their starting "
    "system, any systems they move through, and the active system. They can only "
    "drop units off in the active system where movement ends."
)
_EXAMPLE_A_RECALL = (
    "Ships with sufficient capacity can pick up ground units from their starting "
    "system, any systems they move through, and the active system. However, ships "
    "can only drop off units in the active system (the system where the ship ends "
    "its movement)."
)


def _render_passages(passages) -> str:
    """Render retrieved passages as a delimited, rule-numbered reference block."""
    lines = ["=== RULES REFERENCE (cite these rule numbers) ==="]
    for p in passages:
        topic = p.get("topic", "")
        lines.append(f"[{p.get('rule_id', '')}] {topic}\n{p.get('text', '').strip()}")
    lines.append("=== END RULES REFERENCE ===")
    return "\n\n".join(lines)


def build_messages(question: str, passages=None):
    """Build the chat messages for a rules question.

    When ``passages`` is a non-empty list of retrieved rule chunks
    (``{rule_id, topic, text, ...}``), builds the grounded rules_chat_v3 prompt;
    otherwise the pre-RAG rules_chat_v2 prompt.
    """
    if passages:
        system = _SYSTEM_BASE + _SYSTEM_GROUNDED
        example_a = _EXAMPLE_A_GROUNDED
        human = f"{_render_passages(passages)}\n\nQuestion: {question}"
    else:
        system = _SYSTEM_BASE + _SYSTEM_RECALL
        example_a = _EXAMPLE_A_RECALL
        human = question

    return [
        SystemMessage(content=system),
        HumanMessage(content=_EXAMPLE_Q),
        AIMessage(content=example_a),
        HumanMessage(content=human),
    ]
