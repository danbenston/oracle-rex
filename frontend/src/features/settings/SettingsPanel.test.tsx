import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SettingsProvider } from '../../store/settings'
import { SettingsPanel } from './SettingsPanel'

function renderPanel() {
  return render(
    <SettingsProvider>
      <SettingsPanel />
    </SettingsProvider>,
  )
}

describe('SettingsPanel', () => {
  it('renders a model group per feature with the recommended model checked', () => {
    renderPanel()
    for (const heading of [
      'Rules Q&A',
      'Strategy Suggester',
      'Move Suggester',
      'Tactical Calculator',
    ]) {
      expect(screen.getByText(heading)).toBeInTheDocument()
    }
    // Strategy defaults to Gemini. The same model labels appear in the Move
    // group too, so scope the query to the Strategy fieldset (a11y "group").
    const strategyGroup = screen.getByRole('group', { name: 'Strategy Suggester' })
    expect(
      within(strategyGroup).getByRole('radio', { name: /Gemini 3\.1 Flash-Lite/ }),
    ).toBeChecked()
  })

  it('lets the user change a feature model selection', () => {
    renderPanel()
    const strategyGroup = screen.getByRole('group', { name: 'Strategy Suggester' })
    const claude = within(strategyGroup).getByRole('radio', {
      name: 'Claude Sonnet 5 (mid)',
    })
    expect(claude).not.toBeChecked()
    fireEvent.click(claude)
    expect(claude).toBeChecked()
  })

  it('updates the API key and access code inputs', () => {
    renderPanel()
    const key = screen.getByLabelText(/openai api key/i)
    fireEvent.change(key, { target: { value: 'sk-test' } })
    expect(key).toHaveValue('sk-test')

    const code = screen.getByLabelText(/live demo access code/i)
    fireEvent.change(code, { target: { value: 'abc123' } })
    expect(code).toHaveValue('abc123')
  })

  it('does not claim keys are persisted to local storage', () => {
    renderPanel()
    // The legacy copy wrongly warned about local storage; keys are in-memory.
    expect(
      screen.getByText(/kept in memory for this browser tab only/i),
    ).toBeInTheDocument()
  })
})
