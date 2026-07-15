import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { createQueryClient } from '../../providers/queryClient'
import { SettingsProvider } from '../../store/settings'
import { useSettings } from '../../store/settingsContext'
import { jobDict, sampleCatalog } from '../../test/fixtures'
import { server } from '../../test/server'
import { BattleCalculator } from './BattleCalculator'

// Catalog with a runnable tac_calc battle scenario (the App fixture only covers
// strategy/rules), so the demo path has unit_counts to apply.
const catalogWithBattle = {
  ...sampleCatalog,
  scenarios: {
    ...sampleCatalog.scenarios,
    tac_calc: {
      feature: 'tac_calc',
      title: 'Load Example Battle',
      description: 'A mid-game space battle.',
      key: 'sample_battle',
      response_key: 'sample_battle_result',
      unit_counts: { 'friendly-dreadnought': 1, 'enemy-fighter': 4 },
    },
  },
}

// A deterministic-sim response matching battleSimSchema.
const simResult = (overrides = {}) => ({
  win_probability: 0.64,
  win_percent: 64,
  minimum_fleet: { cruiser: 2 },
  recommended_fleet: { cruiser: 3, dreadnought: 1 },
  breakdown: {
    trials: 10000,
    planet_invasion_required: false,
    blocked_no_ground: false,
    notes: ['Base unit stats; unit-upgrade techs are not modeled.'],
  },
  ...overrides,
})

// Tactical defaults to Gemini (server-keyed, no key). seed-model switches it to a
// BYOK model so the no-key credential path can be exercised.
function SeedKey() {
  const { setApiKey, setModel } = useSettings()
  return (
    <>
      <button type="button" onClick={() => setModel('tactical', 'gpt-5.6-terra')}>
        seed-model
      </button>
      <button type="button" onClick={() => setApiKey('openai', 'sk-test')}>
        seed-key
      </button>
    </>
  )
}

function renderCalculator() {
  const client = createQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <SettingsProvider>
        <SeedKey />
        <BattleCalculator />
      </SettingsProvider>
    </QueryClientProvider>,
  )
}

describe('BattleCalculator', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/demo/catalog/', () => HttpResponse.json(catalogWithBattle)),
      http.get('/api/demo/status/', () =>
        HttpResponse.json({ live_demo_enabled: false }),
      ),
    )
  })

  it('computes the deterministic result with no API key', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(
      http.post('/api/tactical/simulate/', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(simResult())
      }),
    )

    renderCalculator()

    const friendlyFleet = screen.getByRole('group', { name: 'Friendly Fleet' })
    const enemyFleet = screen.getByRole('group', { name: 'Enemy Fleet' })
    const incCruiser = within(friendlyFleet).getByRole('button', {
      name: /increase cruiser/i,
    })
    fireEvent.click(incCruiser)
    fireEvent.click(incCruiser)
    fireEvent.click(
      within(enemyFleet).getByRole('button', { name: /increase cruiser/i }),
    )

    fireEvent.click(screen.getByRole('button', { name: /^calculate$/i }))

    await waitFor(() => expect(screen.getByText('64%')).toBeInTheDocument())
    // Recommended fleet rendered.
    expect(screen.getByText(/3 Cruiser, 1 Dreadnought/i)).toBeInTheDocument()
    expect(captured?.force_data).toEqual({
      friendly_fleet: { cruiser: 2 },
      enemy_fleet: { cruiser: 1 },
      friendly_ground_forces: {},
      enemy_ground_forces_and_structures: {},
    })
  })

  it('caps a limited unit (War Sun) at its per-player component limit', () => {
    renderCalculator()
    const friendlyFleet = screen.getByRole('group', { name: 'Friendly Fleet' })
    const incWarSun = within(friendlyFleet).getByRole('button', {
      name: /increase war sun/i,
    })
    // War Sun is capped at 2: after two clicks the increase control disables and
    // the count cannot go past the limit.
    fireEvent.click(incWarSun)
    fireEvent.click(incWarSun)
    expect(incWarSun).toBeDisabled()
    expect(within(friendlyFleet).getByLabelText('War Sun count')).toHaveTextContent('2')
  })

  it('does not call the AI job when the explanation box is unchecked', async () => {
    let jobCalled = false
    server.use(
      http.post('/api/tactical/simulate/', () => HttpResponse.json(simResult())),
      http.post('/api/jobs/tactical/', () => {
        jobCalled = true
        return HttpResponse.json({ job_id: 'x', status: 'queued' }, { status: 202 })
      }),
    )

    renderCalculator()
    fireEvent.click(screen.getByRole('button', { name: 'seed-key' }))
    fireEvent.click(screen.getByRole('button', { name: /^calculate$/i }))

    await waitFor(() => expect(screen.getByText('64%')).toBeInTheDocument())
    expect(jobCalled).toBe(false)
  })

  it('seeds the LLM job with the computed numbers when the box is checked', async () => {
    let jobBody: Record<string, unknown> | undefined
    server.use(
      http.post('/api/tactical/simulate/', () => HttpResponse.json(simResult())),
      http.post('/api/jobs/tactical/', async ({ request }) => {
        jobBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ job_id: 'job-1', status: 'queued' }, { status: 202 })
      }),
      http.get('/api/jobs/job-1/', () =>
        HttpResponse.json(
          jobDict({
            id: 'job-1',
            feature_type: 'tac_calc',
            status: 'completed',
            is_terminal: true,
            result: { calc_results: 'The dreadnoughts carry this fight.' },
          }),
        ),
      ),
    )

    renderCalculator()
    fireEvent.click(screen.getByRole('button', { name: 'seed-key' }))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /^calculate$/i }))

    await waitFor(() =>
      expect(screen.getByText(/dreadnoughts carry this fight/i)).toBeInTheDocument(),
    )
    expect(screen.getByText('64%')).toBeInTheDocument()
    expect(jobBody?.force_data).toBeTruthy()
    expect((jobBody?.simulation as { win_percent?: number })?.win_percent).toBe(64)
  })

  it('shows credential guidance (but still the result) when checked with no key', async () => {
    server.use(
      http.post('/api/tactical/simulate/', () => HttpResponse.json(simResult())),
    )

    renderCalculator()
    fireEvent.click(screen.getByRole('button', { name: 'seed-model' }))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /^calculate$/i }))

    await waitFor(() => expect(screen.getByText('64%')).toBeInTheDocument())
    expect(screen.getByText(/no api key found/i)).toBeInTheDocument()
  })

  it('loads the example battle and computes it live', async () => {
    server.use(
      http.post('/api/tactical/simulate/', () =>
        HttpResponse.json(simResult({ win_percent: 71 })),
      ),
    )

    renderCalculator()
    const demoButton = await screen.findByRole('button', {
      name: /load example battle/i,
    })
    await waitFor(() => expect(demoButton).toBeEnabled())
    fireEvent.click(demoButton)

    await waitFor(() => expect(screen.getByText('71%')).toBeInTheDocument())
    // The scenario's counts were applied.
    const friendlyFleet = screen.getByRole('group', { name: 'Friendly Fleet' })
    const dreadnought = within(friendlyFleet).getByRole('group', {
      name: 'Dreadnought',
    })
    expect(within(dreadnought).getByText('1')).toBeInTheDocument()
  })
})
