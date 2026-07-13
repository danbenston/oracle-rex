import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { createQueryClient } from '../../providers/queryClient'
import { SettingsProvider } from '../../store/settings'
import { useSettings } from '../../store/settingsContext'
import { completedRulesResult, jobDict, sampleCatalog } from '../../test/fixtures'
import { server } from '../../test/server'
import { RulesPanel } from './RulesPanel'

// Rules defaults to Gemini (server-keyed, no user key). These buttons switch it
// to a BYOK model and seed an OpenAI key, so the key / no-key paths can be tested.
function Seeds() {
  const { setApiKey, setModel } = useSettings()
  return (
    <>
      <button type="button" onClick={() => setModel('rules', 'gpt-5.4-nano')}>
        seed-model
      </button>
      <button type="button" onClick={() => setApiKey('openai', 'sk-test')}>
        seed-key
      </button>
    </>
  )
}

function renderPanel() {
  const client = createQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <SettingsProvider>
        <Seeds />
        <RulesPanel />
      </SettingsProvider>
    </QueryClientProvider>,
  )
}

describe('RulesPanel', () => {
  beforeEach(() => {
    // sampleCatalog carries a rules scenario with one chip (rules_retreat).
    server.use(
      http.get('/api/demo/catalog/', () => HttpResponse.json(sampleCatalog)),
      http.get('/api/demo/status/', () =>
        HttpResponse.json({ live_demo_enabled: false }),
      ),
    )
  })

  it('asks the user to add a key when asking with a BYOK model and no credentials', () => {
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'seed-model' }))
    fireEvent.change(screen.getByLabelText(/rules question/i), {
      target: { value: 'Can I retreat?' },
    })
    fireEvent.click(screen.getByRole('button', { name: /ask oracle rex/i }))
    expect(screen.getByText(/no api key found/i)).toBeInTheDocument()
  })

  it('disables Ask until a question is typed', () => {
    renderPanel()
    const ask = screen.getByRole('button', { name: /ask oracle rex/i })
    expect(ask).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/rules question/i), {
      target: { value: 'Hi' },
    })
    expect(ask).toBeEnabled()
  })

  it('submits the question and renders the structured rules answer', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(
      http.post('/api/jobs/rules/', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ job_id: 'job-1', status: 'queued' }, { status: 202 })
      }),
      http.get('/api/jobs/job-1/', () =>
        HttpResponse.json(
          jobDict({
            id: 'job-1',
            status: 'completed',
            is_terminal: true,
            result: completedRulesResult,
          }),
        ),
      ),
    )

    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'seed-model' }))
    fireEvent.click(screen.getByRole('button', { name: 'seed-key' }))
    fireEvent.change(screen.getByLabelText(/rules question/i), {
      target: { value: '  Can I retreat with no ships?  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /ask oracle rex/i }))

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /rules answer/i }),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/Space combat, Retreat step/)).toBeInTheDocument()
    // Grounded answer shows a cited-rule chip; tapping it reveals the exact
    // rule text from the job payload's passages (no second request).
    const chip = screen.getByRole('button', { name: /LRR 78\.7 · Space Combat/ })
    expect(screen.queryByText(/STEP 5-RETREAT/)).not.toBeInTheDocument()
    fireEvent.click(chip)
    expect(screen.getByText(/STEP 5-RETREAT/)).toBeInTheDocument()
    // Question is trimmed before submission.
    expect(captured?.question).toBe('Can I retreat with no ships?')
    expect(captured?.api_key).toBe('sk-test')
  })

  it('runs a demo chip through the poll UI with a demo label and fills the question', async () => {
    server.use(
      http.post('/api/demo/run/', () =>
        HttpResponse.json({ job_id: 'demo-1', status: 'completed' }, { status: 202 }),
      ),
      http.get('/api/jobs/demo-1/', () =>
        HttpResponse.json(
          jobDict({
            id: 'demo-1',
            status: 'completed',
            is_terminal: true,
            result: {
              ...completedRulesResult,
              demo: true,
              demo_label: 'Demo response generated from a saved scenario.',
            },
          }),
        ),
      ),
    )

    renderPanel()
    const chip = await screen.findByRole('button', { name: /can i retreat\?/i })
    fireEvent.click(chip)

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /rules answer/i }),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText(/saved scenario/i)).toBeInTheDocument()
    // The chip's question was copied into the input.
    expect(screen.getByLabelText(/rules question/i)).toHaveValue('Can I retreat?')
  })
})
