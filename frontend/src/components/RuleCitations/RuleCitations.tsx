import { useState } from 'react'

import type { RulePassage, RulesAnswer } from '../../types/ai'
import styles from './RuleCitations.module.css'

// Grounded Rules Q&A citations UX (RAG Phase 3). Renders under the rules answer
// card: either the cited LRR rules as expandable chips (tap to reveal the exact
// rule text, which comes from the retrieved `passages`, never the model), or,
// for an ungrounded answer, a visible "answered from general knowledge" note so
// it never silently looks authoritative.

const LRR_ATTRIBUTION = 'Source: Living Rules Reference 2.0 · Fantasy Flight Games'

export interface RuleCitationsProps {
  answer: RulesAnswer
  passages: RulePassage[]
}

export function RuleCitations({ answer, passages }: RuleCitationsProps) {
  const [openId, setOpenId] = useState<string | null>(null)

  if (!answer.grounded) {
    return (
      <p className={styles.ungrounded} role="note">
        Answered from general knowledge; no matching rules text was found (this may be
        Discordant Stars or other out-of-reference content).
      </p>
    )
  }

  if (answer.citations.length === 0) return null

  const byId = new Map(passages.map((p) => [p.rule_id, p]))

  return (
    <div className={styles.citations}>
      <p className={styles.heading}>Cited rules</p>
      <ul className={styles.list}>
        {answer.citations.map((citation) => {
          const passage = byId.get(citation.rule_id)
          const topic = passage?.topic
          const label = topic
            ? `LRR ${citation.rule_id} · ${topic}`
            : `LRR ${citation.rule_id}`
          const ruleText = passage?.text?.trim()
          const isOpen = openId === citation.rule_id
          return (
            <li key={citation.rule_id} className={styles.item}>
              <button
                type="button"
                className={styles.chip}
                aria-expanded={ruleText ? isOpen : undefined}
                disabled={!ruleText}
                onClick={() => setOpenId(isOpen ? null : citation.rule_id)}
              >
                {label}
              </button>
              {citation.relevance && (
                <span className={styles.relevance}>{citation.relevance}</span>
              )}
              {isOpen && ruleText && <p className={styles.ruleText}>{ruleText}</p>}
            </li>
          )
        })}
      </ul>
      <p className={styles.attribution}>{LRR_ATTRIBUTION}</p>
    </div>
  )
}
