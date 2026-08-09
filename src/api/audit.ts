import type { AnnotationImportHistoryPayload, AuditSummary, RebuildPreview } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchAuditSummary(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/audit`)
  return parseJsonResponse<AuditSummary>(response)
}

export async function previewRebuild(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/rebuild/preview`, { method: 'POST' })
  return parseJsonResponse<RebuildPreview>(response)
}

export async function fetchAnnotationImports(projectId: string, documentId?: string, limit = 5) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (documentId) params.set('document_id', documentId)
  const response = await fetch(`/api/projects/${projectId}/annotation-imports?${params.toString()}`)
  const payload = await parseJsonResponse<AnnotationImportHistoryPayload>(response)
  return {
    imports: payload.imports.map((item) => ({
      ...item,
      import_filename: item.import_filename || item.filename,
    })),
  }
}
