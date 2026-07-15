import type { JobFeature } from '../types/ai'

// The per-feature AI model catalog. Originally mirrored from the legacy Settings
// tab (templates/settings.html); each radio group is `<feature>-ai-model` with a
// `data-api-make` per option, which here becomes a typed option list plus the
// default. The settings store uses `apiMake` to pick which BYOK key to send for
// the selected model.
//
// Ordering convention: every group runs fastest -> most capable, so the two fast
// low-latency models (FAST_OPTIONS) always lead, even when they are not the
// default selection for that feature.

/** Which provider key a model is billed against (the legacy `data-api-make`).
 * `google` (Gemini) is special: its key lives on the server, so a Google model
 * needs no BYOK key from the user (see the settings store's getCredentials). */
export type ApiMake = 'openai' | 'xai' | 'anthropic' | 'google'

// The four AI features exposed in Settings. These line up 1:1 with the
// job-create URL segments (JobFeature), so a feature's selection can be sent
// straight to `createJob(feature, …)`.
export type SettingsFeature = JobFeature

export interface ModelOption {
  value: string
  label: string
  apiMake: ApiMake
}

export interface FeatureModelGroup {
  feature: SettingsFeature
  heading: string
  options: ModelOption[]
  /** The default-selected model value (the legacy `checked` radio). */
  defaultValue: string
}

// Gemini leads every group and is the default for all features: it is fast and
// runs on the server-held key, so a user gets live AI with no key of their own.
// Keep this value identical to GEMINI_MODELS in core/service/ai/config.py.
const GEMINI: ModelOption = {
  value: 'gemini-3.1-flash-lite',
  label: 'Gemini 3.1 Flash-Lite',
  apiMake: 'google',
}

// Paid (BYOK) models carry a plain low/mid/high tier suffix rather than a
// per-model descriptor, so the ladder reads the same in every group and a model
// swap doesn't strand a bespoke label. The tier tracks capability, which is also
// the ordering key. Gemini keeps its own label: it is the free server-keyed
// option and sits outside the paid ladder.
const FAST_OPTIONS: ModelOption[] = [
  GEMINI,
  { value: 'gpt-5.4-nano', label: 'GPT-5.4 nano (low)', apiMake: 'openai' },
  { value: 'claude-haiku-4-5', label: 'Claude Haiku 4.5 (low)', apiMake: 'anthropic' },
]

// Strategy + Move share the same option list, ordered fastest -> most capable.
const strategyMoveOptions: ModelOption[] = [
  ...FAST_OPTIONS,
  { value: 'gpt-5.6-terra', label: 'GPT-5.6 Terra (mid)', apiMake: 'openai' },
  { value: 'grok-4.5', label: 'Grok 4.5 (mid)', apiMake: 'xai' },
  { value: 'claude-sonnet-5', label: 'Claude Sonnet 5 (mid)', apiMake: 'anthropic' },
  { value: 'claude-opus-4-8', label: 'Claude Opus 4.8 (high)', apiMake: 'anthropic' },
  { value: 'gpt-5.6-sol', label: 'GPT-5.6 Sol (high)', apiMake: 'openai' },
]

// Ordered to match the Settings tab layout (Rules, Strategy, Move, Tactical).
export const FEATURE_MODEL_GROUPS: FeatureModelGroup[] = [
  {
    feature: 'rules',
    heading: 'Rules Q&A',
    defaultValue: 'gemini-3.1-flash-lite',
    // Grounded Q&A puts retrieved rules passages in the prompt and wants a short
    // cited answer, so the group tops out at mid: cheap input and faithful
    // formatting matter more here than deep reasoning.
    options: [
      ...FAST_OPTIONS,
      { value: 'grok-4.3', label: 'Grok 4.3 (low)', apiMake: 'xai' },
      { value: 'gpt-5.6-luna', label: 'GPT-5.6 Luna (mid)', apiMake: 'openai' },
    ],
  },
  {
    feature: 'strategy',
    heading: 'Strategy Suggester',
    defaultValue: 'gemini-3.1-flash-lite',
    options: strategyMoveOptions,
  },
  {
    feature: 'move',
    heading: 'Move Suggester',
    defaultValue: 'gemini-3.1-flash-lite',
    options: strategyMoveOptions,
  },
  {
    feature: 'tactical',
    heading: 'Tactical Calculator',
    defaultValue: 'gemini-3.1-flash-lite',
    options: [
      ...FAST_OPTIONS,
      { value: 'gpt-5.6-terra', label: 'GPT-5.6 Terra (mid)', apiMake: 'openai' },
      { value: 'grok-4.5', label: 'Grok 4.5 (mid)', apiMake: 'xai' },
      {
        value: 'claude-sonnet-5',
        label: 'Claude Sonnet 5 (mid)',
        apiMake: 'anthropic',
      },
      { value: 'gpt-5.6-sol', label: 'GPT-5.6 Sol (high)', apiMake: 'openai' },
    ],
  },
]

/** Default model selection per feature, derived from the catalog. */
export const DEFAULT_MODELS: Record<SettingsFeature, string> =
  FEATURE_MODEL_GROUPS.reduce(
    (acc, group) => {
      acc[group.feature] = group.defaultValue
      return acc
    },
    {} as Record<SettingsFeature, string>,
  )

/** Look up which provider key a feature's selected model bills against. */
export function apiMakeFor(feature: SettingsFeature, model: string): ApiMake {
  const group = FEATURE_MODEL_GROUPS.find((g) => g.feature === feature)
  const option = group?.options.find((o) => o.value === model)
  return option?.apiMake ?? 'openai'
}
