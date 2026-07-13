import { AdvisorCard } from '../AdvisorCard/AdvisorCard'
import {
  moveCard,
  parseStructured,
  rulesCard,
  strategyCard,
} from '../AdvisorCard/advisorSections'
import { RuleCitations } from '../RuleCitations/RuleCitations'
import type { FeatureType, JobResult, RulesAnswer } from '../../types/ai'

// The shared result renderer: structured payloads (rules / strategy / move)
// render as an AdvisorCard; tac_calc (and any response without valid structured
// data) falls back to the plain-text result field. A demo label is shown
// whenever the result is tagged as a demo response.

export interface JobResultViewProps {
  feature: FeatureType
  result: JobResult
}

/** The plain-text field the worker returns for each feature. */
function fallbackText(feature: FeatureType, result: JobResult): string {
  switch (feature) {
    case 'rules':
      return result.answer ?? 'No answer was returned.'
    case 'strategy':
    case 'move':
      return result.strategy ?? 'No recommendation was returned.'
    case 'tac_calc':
      return result.calc_results ?? 'No result was returned.'
    default:
      return 'No result was returned.'
  }
}

export function JobResultView({ feature, result }: JobResultViewProps) {
  const demoLabel = result.demo ? (result.demo_label ?? '') || undefined : undefined
  const structured = parseStructured(feature, result.structured)

  if (structured) {
    // Rules answers render the card plus a grounding block: cited LRR rules as
    // expandable chips (exact text from result.passages), or an ungrounded note.
    if (feature === 'rules') {
      const answer = structured as RulesAnswer
      return (
        <>
          <AdvisorCard {...rulesCard(answer)} demoLabel={demoLabel} />
          <RuleCitations answer={answer} passages={result.passages ?? []} />
        </>
      )
    }
    const props =
      feature === 'strategy'
        ? strategyCard(structured as Parameters<typeof strategyCard>[0])
        : moveCard(structured as Parameters<typeof moveCard>[0])
    return <AdvisorCard {...props} demoLabel={demoLabel} />
  }

  // Text fallback (tac_calc, or a response missing valid structured data).
  const statusLabel = feature === 'tac_calc' ? 'COMBAT ODDS' : 'RESPONSE'
  return (
    <AdvisorCard
      text={fallbackText(feature, result)}
      statusLabel={statusLabel}
      demoLabel={demoLabel}
    />
  )
}
