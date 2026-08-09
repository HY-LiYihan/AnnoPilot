import type { RuntimeHealth } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchRuntimeHealth() {
  const response = await fetch('/api/health')
  return parseJsonResponse<RuntimeHealth>(response)
}
