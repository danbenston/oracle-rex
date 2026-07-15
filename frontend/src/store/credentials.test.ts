import { describe, expect, it } from 'vitest'

import { buildLiveCredentials, NO_CREDENTIALS_MESSAGE } from './credentials'

describe('buildLiveCredentials', () => {
  it('prefers an access code over a BYOK key', () => {
    const result = buildLiveCredentials({
      accessCode: ' let-me-in ',
      apiKey: 'sk-123',
      model: 'gpt-4',
    })
    expect(result).toEqual({ creds: { access_code: 'let-me-in' } })
  })

  it('uses the BYOK key and model when no access code is given', () => {
    const result = buildLiveCredentials({ apiKey: ' sk-123 ', model: 'gpt-5.6-terra' })
    expect(result).toEqual({ creds: { api_key: 'sk-123', model: 'gpt-5.6-terra' } })
  })

  it('returns a helpful error when neither credential is present', () => {
    const result = buildLiveCredentials({ model: 'gpt-4' })
    expect(result).toEqual({ error: NO_CREDENTIALS_MESSAGE })
  })

  it('treats whitespace-only inputs as empty', () => {
    const result = buildLiveCredentials({
      accessCode: '   ',
      apiKey: '  ',
      model: 'gpt-4',
    })
    expect(result.error).toBe(NO_CREDENTIALS_MESSAGE)
  })
})
