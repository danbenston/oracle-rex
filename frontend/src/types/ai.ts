import type { z } from 'zod'

import type {
  battleSimSchema,
  featureTypeSchema,
  jobCreatedSchema,
  jobResultSchema,
  jobStatusSchema,
  jobStatusValueSchema,
  ruleCitationSchema,
  rulePassageSchema,
  rulesAnswerSchema,
  strategicPlanSchema,
  tacticalMoveSchema,
} from '../schemas/ai.zod'
import type { Game } from './game'

// Domain types for the async AI job contract. Response shapes are inferred from
// the zod schemas; request/credential shapes are hand-written (they are sent,
// not validated on receipt).

// Backend feature_type stored on the job row (`tac_calc`, not `tactical`).
export type FeatureType = z.infer<typeof featureTypeSchema>
export type JobStatusValue = z.infer<typeof jobStatusValueSchema>

export type RulesAnswer = z.infer<typeof rulesAnswerSchema>
export type RuleCitation = z.infer<typeof ruleCitationSchema>
export type RulePassage = z.infer<typeof rulePassageSchema>
export type StrategicPlan = z.infer<typeof strategicPlanSchema>
export type TacticalMove = z.infer<typeof tacticalMoveSchema>

export type BattleSimResult = z.infer<typeof battleSimSchema>

export type JobResult = z.infer<typeof jobResultSchema>
export type JobStatus = z.infer<typeof jobStatusSchema>
export type JobCreated = z.infer<typeof jobCreatedSchema>

// --- Request side ------------------------------------------------------------

// The URL segment used to create a job (`POST /api/jobs/<JobFeature>/`). Note
// `tactical` here maps to the `tac_calc` feature_type on the resulting job row.
export type JobFeature = 'rules' | 'strategy' | 'move' | 'tactical'

// Force composition payload for the battle calculator (mirrors the legacy
// getForceCounts() output). Each map is `{ unit: count }`, omitting zeroes.
export interface ForceData {
  friendly_fleet: Record<string, number>
  enemy_fleet: Record<string, number>
  friendly_ground_forces: Record<string, number>
  enemy_ground_forces_and_structures: Record<string, number>
}

// Credentials sent with a live job-create body. An access code (private live
// demo) takes precedence over a BYOK key. A Google (Gemini) model sends only the
// model: the server holds that key, so no api_key or access code is needed.
// Exactly one variant is sent.
export type LiveCredentials =
  | { access_code: string }
  | { api_key: string; model: string }
  | { model: string }

// Per-feature job inputs (before credentials are merged in).
export interface RulesJobInput {
  question: string
}
export interface SuggestJobInput {
  game_json: Game
  player_faction: string
}
export interface TacticalJobInput {
  force_data: ForceData
  /** The deterministic simulation result the LLM explains (M6C); not computed. */
  simulation?: BattleSimResult
}

export type JobInput = RulesJobInput | SuggestJobInput | TacticalJobInput
