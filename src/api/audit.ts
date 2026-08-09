import type { AuditSummary, RebuildPreview } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchAuditSummary(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/audit`)
  return parseJsonResponse<AuditSummary>(response)
}

export async function previewRebuild(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/rebuild/preview`, { method: 'POST' })
  return parseJsonResponse<RebuildPreview>(response)
}
