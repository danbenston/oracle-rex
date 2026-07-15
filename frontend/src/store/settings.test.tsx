import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ReactNode } from 'react'

import { NO_CREDENTIALS_MESSAGE } from './credentials'
import { SettingsProvider } from './settings'
import { useSettings } from './settingsContext'

const wrapper = ({ children }: { children: ReactNode }) => (
  <SettingsProvider>{children}</SettingsProvider>
)

describe('settings store', () => {
  it('defaults each feature to its recommended model', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })
    expect(result.current.models).toEqual({
      rules: 'gemini-3.1-flash-lite',
      strategy: 'gemini-3.1-flash-lite',
      move: 'gemini-3.1-flash-lite',
      tactical: 'gemini-3.1-flash-lite',
    })
  })

  it('defaults persona to none and lets the user change it', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })
    expect(result.current.persona).toBe('default')
    act(() => result.current.setPersona('oracle'))
    expect(result.current.persona).toBe('oracle')
  })

  it('needs no key for a Google (Gemini) model: sends just the model', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })
    // Strategy defaults to Gemini (server-keyed), so it is ready with no key.
    expect(result.current.getCredentials('strategy')).toEqual({
      creds: { model: 'gemini-3.1-flash-lite' },
    })
  })

  it('errors when a BYOK model is selected with no credential entered', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })
    // The default Gemini model needs no key; a BYOK model with no key errors.
    act(() => result.current.setModel('strategy', 'gpt-5.6-terra'))
    expect(result.current.getCredentials('strategy')).toEqual({
      error: NO_CREDENTIALS_MESSAGE,
    })
  })

  it('sends the BYOK key matching the selected model provider', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })

    // Pick a BYOK OpenAI model for strategy (the default is now Gemini), so the
    // OpenAI key should be used.
    act(() => result.current.setModel('strategy', 'gpt-5.6-terra'))
    act(() => result.current.setApiKey('openai', 'sk-openai'))
    expect(result.current.getCredentials('strategy')).toEqual({
      creds: { api_key: 'sk-openai', model: 'gpt-5.6-terra' },
    })

    // Switching strategy to a Claude model should now require the Anthropic key.
    act(() => result.current.setModel('strategy', 'claude-sonnet-5'))
    expect(result.current.getCredentials('strategy')).toEqual({
      error: NO_CREDENTIALS_MESSAGE,
    })

    act(() => result.current.setApiKey('anthropic', 'sk-anthropic'))
    expect(result.current.getCredentials('strategy')).toEqual({
      creds: { api_key: 'sk-anthropic', model: 'claude-sonnet-5' },
    })
  })

  it('prefers the access code over any BYOK key', () => {
    const { result } = renderHook(() => useSettings(), { wrapper })
    act(() => {
      result.current.setApiKey('openai', 'sk-openai')
      result.current.setAccessCode(' let-me-in ')
    })
    expect(result.current.getCredentials('rules')).toEqual({
      creds: { access_code: 'let-me-in' },
    })
  })
})
