import type { Game } from '../types/game'
import type { JobStatus } from '../types/ai'
import type { DemoCatalog } from '../types/demo'

// Minimal, schema-valid fixtures mirroring the real backend payloads, for the
// MSW-mocked client and hook tests.

export const sampleGame: Game = {
  name: 'strategy',
  players: [
    { username: 'strategy player 1', faction: 'sol', starting_position: '1-1' },
    { username: 'strategy player 2', faction: 'ul', starting_position: '1-4' },
  ],
  board: [
    {
      designation: '0-0',
      adjacent_tiles: ['1-0', '1-1'],
      system: {
        name: 'Mecatol Rex System',
        tile_id: '18',
        anomaly: 'none',
        wormhole: 'none',
        planets: [
          {
            name: 'Mecatol Rex',
            resources: 1,
            influence: 6,
            trait: 'none',
            tech_specialty: 'none',
            legendary: false,
            ground_forces: null,
          },
        ],
        fleet: null,
      },
    },
  ],
}

export function jobDict(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    id: 'job-1',
    feature_type: 'rules',
    status: 'queued',
    is_terminal: false,
    result: null,
    error: null,
    model_name: 'gpt-5.4-nano',
    prompt_version: 'rules@1',
    created_at: '2026-06-15T00:00:00Z',
    completed_at: null,
    ...overrides,
  }
}

export const completedRulesResult = {
  answer: 'No, you must have at least one ship to retreat.',
  structured: {
    answer: 'No, a retreat moves surviving ships, so you need at least one.',
    assumptions: ['Standard space combat retreat.'],
    rule_basis: ['Space combat, Retreat step'],
    caveats: ['Must be announced before hits resolve.'],
    citations: [
      {
        rule_id: '78.7',
        relevance: 'A retreat needs surviving ships and an eligible system',
      },
    ],
    grounded: true,
    needs_exact_text: false,
  },
  passages: [
    {
      rule_id: '78.7',
      topic: 'Space Combat',
      text: 'STEP 5-RETREAT: If a player announced a retreat and there is still an eligible system, they must retreat.',
      score: 9.1,
    },
    {
      rule_id: '78.4',
      topic: 'Space Combat',
      text: 'STEP 2-ANNOUNCE RETREATS: Each player may announce a retreat, beginning with the defender.',
      score: 8.2,
    },
  ],
}

export const ungroundedRulesResult = {
  answer: 'Discordant Stars content is not in the LRR; from general knowledge...',
  structured: {
    answer:
      'That is Discordant Stars content, which is not in the Living Rules Reference.',
    assumptions: [],
    rule_basis: [],
    caveats: [],
    citations: [],
    grounded: false,
    needs_exact_text: false,
  },
  passages: [],
}

export const demoTacticalResult = {
  calc_results: 'Odds of Victory: 71%',
  demo: true,
  demo_label: 'Demo response generated from a saved scenario.',
}

export const sampleCatalog: DemoCatalog = {
  label: 'Demo response generated from a saved scenario.',
  scenarios: {
    strategy: {
      feature: 'strategy',
      title: 'Load Sample Milty Draft Board',
      description: 'A balanced 6-player Milty Draft board.',
      key: 'sample_opening_board',
      response_key: 'sample_opening_strategy',
      tts_string: '78 40 42',
      suggested_faction: 'sol',
    },
    rules: {
      feature: 'rules',
      title: 'Sample Rules Questions',
      description: 'One-click rules questions.',
      chips: [
        {
          key: 'rules_retreat',
          question: 'Can I retreat?',
          response_key: 'sample_rules_retreat',
        },
      ],
    },
  },
}
