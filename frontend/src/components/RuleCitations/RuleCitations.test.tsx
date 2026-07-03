import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { RulePassage, RulesAnswer } from '../../types/ai'
import { RuleCitations } from './RuleCitations'

const passages: RulePassage[] = [
  {
    rule_id: '78.7',
    topic: 'Space Combat',
    text: 'STEP 5-RETREAT: if there is still an eligible system, they must retreat.',
  },
]

function answer(over: Partial<RulesAnswer> = {}): RulesAnswer {
  return {
    answer: '',
    assumptions: [],
    rule_basis: [],
    caveats: [],
    citations: [],
    grounded: false,
    needs_exact_text: false,
    ...over,
  }
}

describe('RuleCitations', () => {
  it('renders cited rules as chips labelled with their topic', () => {
    render(
      <RuleCitations
        answer={answer({
          grounded: true,
          citations: [{ rule_id: '78.7', relevance: 'needs an eligible system' }],
        })}
        passages={passages}
      />,
    )
    expect(screen.getByText('Cited rules')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /LRR 78\.7 · Space Combat/ }),
    ).toBeInTheDocument()
    expect(screen.getByText('needs an eligible system')).toBeInTheDocument()
    expect(screen.getByText(/Living Rules Reference 2\.0/)).toBeInTheDocument()
  })

  it('expands and collapses the exact rule text on click', () => {
    render(
      <RuleCitations
        answer={answer({
          grounded: true,
          citations: [{ rule_id: '78.7', relevance: '' }],
        })}
        passages={passages}
      />,
    )
    const chip = screen.getByRole('button', { name: /LRR 78\.7/ })
    expect(screen.queryByText(/STEP 5-RETREAT/)).not.toBeInTheDocument()

    fireEvent.click(chip)
    expect(screen.getByText(/STEP 5-RETREAT/)).toBeInTheDocument()
    expect(chip).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(chip)
    expect(screen.queryByText(/STEP 5-RETREAT/)).not.toBeInTheDocument()
  })

  it('shows an ungrounded note (and no citations) when not grounded', () => {
    render(<RuleCitations answer={answer({ grounded: false })} passages={[]} />)
    expect(screen.getByText(/general knowledge/i)).toBeInTheDocument()
    expect(screen.queryByText('Cited rules')).not.toBeInTheDocument()
  })

  it('disables a citation that has no matching passage text', () => {
    render(
      <RuleCitations
        answer={answer({
          grounded: true,
          citations: [{ rule_id: '99.9', relevance: '' }],
        })}
        passages={passages}
      />,
    )
    expect(screen.getByRole('button', { name: 'LRR 99.9' })).toBeDisabled()
  })
})
