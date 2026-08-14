import type { SuggestionDef } from '../types/domain'
import { parseJsonResponse } from './http'

export type EngagementCandidateGroup = {
  id: string
  run_id: string
  sentence_id: string
  candidate_index: number
  model: string
  temperature: number
  prompt_sha256: string
  explanation: string
  spans: Array<Record<string, unknown>>
  verifier_status: string
  verifier_issues: Array<Record<string, unknown>>
  consistency: Record<string, unknown>
  created_at: string
}

export type GenerateEngagementCandidatesResponse = {
  run_id: string
  candidate_count: number
  sentence_count: number
  groups: EngagementCandidateGroup[]
  suggestions: SuggestionDef[]
}

export async function generateEngagementCandidates(
  projectId: string,
  documentId: string,
  candidateCount = 3,
  temperature = 0.7,
  sentenceId?: string,
) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/engagement/candidates/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_count: candidateCount, temperature, ...(sentenceId ? { sentence_id: sentenceId } : {}) }),
  })
  return parseJsonResponse<GenerateEngagementCandidatesResponse>(response)
}
