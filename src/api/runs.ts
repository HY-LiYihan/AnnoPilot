import type { AnnotationRun } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchRuns(projectId: string, documentId?: string, limit = 5) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (documentId) params.set('document_id', documentId)
  const response = await fetch(`/api/projects/${projectId}/runs?${params.toString()}`)
  return parseJsonResponse<{ runs: AnnotationRun[] }>(response)
}

export function runProvenanceExportUrl(projectId: string, runId: string) {
  return `/api/projects/${projectId}/runs/${runId}/provenance.json`
}
