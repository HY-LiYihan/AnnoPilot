import type { LlmSettingsState } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchLlmSettings() {
  const response = await fetch('/api/settings/llm')
  return parseJsonResponse<LlmSettingsState>(response)
}

export async function updateLlmSettings(modelOptionId: string) {
  const response = await fetch('/api/settings/llm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_option_id: modelOptionId }),
  })
  return parseJsonResponse<LlmSettingsState>(response)
}
