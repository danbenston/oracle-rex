import { z } from 'zod'

// Runtime schemas for the async-AI-job contract (core/views.py + the pydantic
// structured schemas in core/service/ai/schemas/). These validate the job
// status envelope and the per-feature structured result payloads.

// Backend feature_type values as stored on the AIJob row (note `tac_calc`,
// which differs from the `tactical` URL segment used to create the job).
export const featureTypeSchema = z.enum(['rules', 'strategy', 'move', 'tac_calc'])

// AIJob.Status text choices.
export const jobStatusValueSchema = z.enum([
  'queued',
  'running',
  'completed',
  'failed',
  'timeout',
  'validation_failed',
])

// --- Structured result schemas (mirror the pydantic models) ------------------

// A citation to a specific Living Rules Reference rule. The exact quoted text is
// NOT here — it is looked up from the job result's `passages` by rule_id (so the
// text always comes from our index, never the model).
export const ruleCitationSchema = z.object({
  rule_id: z.string(),
  relevance: z.string().default(''),
})

export const rulesAnswerSchema = z.object({
  answer: z.string(),
  assumptions: z.array(z.string()).default([]),
  rule_basis: z.array(z.string()).default([]),
  caveats: z.array(z.string()).default([]),
  citations: z.array(ruleCitationSchema).default([]),
  grounded: z.boolean().default(false),
  needs_exact_text: z.boolean().default(false),
})

// A retrieved rules-reference passage carried in the rules job payload, so the
// UI can show the exact cited rule text with no second request.
export const rulePassageSchema = z
  .object({
    rule_id: z.string(),
    topic: z.string().default(''),
    text: z.string().default(''),
    score: z.number().optional(),
  })
  .loose()

export const strategicPlanSchema = z.object({
  summary: z.string(),
  faction_read: z.string().default(''),
  opening_priorities: z.array(z.string()).default([]),
  round_one_plan: z.string().default(''),
  tech_path: z.array(z.string()).default([]),
  expansion_targets: z.array(z.string()).default([]),
  risks: z.array(z.string()).default([]),
  mistakes_to_avoid: z.array(z.string()).default([]),
})

export const tacticalMoveSchema = z.object({
  recommended_move: z.string(),
  reasoning: z.string().default(''),
  expected_benefit: z.string().default(''),
  combat_risk: z.string().default(''),
  alternative_conservative_move: z.string().default(''),
  alternative_aggressive_move: z.string().default(''),
  assumptions: z.array(z.string()).default([]),
})

// --- Deterministic battle simulation (Milestone 6C) --------------------------

// Response of the synchronous `POST /api/tactical/simulate/`. Fleets are
// `{ unit: count }` maps (same shape as force_data fleets). Loose so an added
// breakdown field never fails the call.
export const battleSimSchema = z
  .object({
    win_probability: z.number(),
    win_percent: z.number(),
    minimum_fleet: z.record(z.string(), z.number()),
    recommended_fleet: z.record(z.string(), z.number()),
    breakdown: z
      .object({
        trials: z.number().optional(),
        planet_invasion_required: z.boolean().optional(),
        blocked_no_ground: z.boolean().optional(),
        notes: z.array(z.string()).default([]),
      })
      .loose(),
  })
  .loose()

// --- Job result envelope -----------------------------------------------------

// Fields present on every result payload tagged as a demo response.
const demoFields = {
  demo: z.boolean().optional(),
  demo_label: z.string().optional(),
}

// The worker returns one of these shapes depending on the feature:
//   rules            -> { answer, structured: RulesAnswer }
//   strategy / move  -> { strategy, structured: StrategicPlan | TacticalMove }
//   tac_calc         -> { calc_results }   (plain text only, no structured)
// `structured` is validated leniently here (z.unknown) and narrowed per-feature
// via parseStructured(); the result is otherwise loose so an added backend field
// never fails the poll.
export const jobResultSchema = z
  .object({
    answer: z.string().optional(),
    strategy: z.string().optional(),
    calc_results: z.string().optional(),
    structured: z.unknown().optional(),
    // Grounded Rules Q&A (Phase 2): the retrieved LRR passages the answer cited,
    // so citations can expand to exact rule text without another request.
    passages: z.array(rulePassageSchema).optional(),
    ...demoFields,
  })
  .loose()

// The poll endpoint (`GET /api/jobs/<uuid>/`) payload.
export const jobStatusSchema = z.object({
  id: z.string(),
  feature_type: featureTypeSchema,
  status: jobStatusValueSchema,
  is_terminal: z.boolean(),
  result: jobResultSchema.nullable(),
  error: z.string().nullable(),
  model_name: z.string(),
  prompt_version: z.string(),
  created_at: z.string().nullable(),
  completed_at: z.string().nullable(),
})

// The create endpoints (`POST /api/jobs/<feature>/`, `POST /api/demo/run/`)
// reply 202 with the new job id.
export const jobCreatedSchema = z.object({
  job_id: z.string(),
  status: z.string(),
})
