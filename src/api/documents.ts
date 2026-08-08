import type { DocumentPayload, ImportTxtResponse } from '../types/domain'
import { parseJsonResponse, responseMessage } from './http'

export async function importTxt(projectId: string, file: File): Promise<ImportTxtResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`/api/projects/${projectId}/import-txt`, {
    method: 'POST',
    body: formData,
  })
  return parseJsonResponse<ImportTxtResponse>(response)
}

export async function fetchDocument(projectId: string, documentId: string): Promise<DocumentPayload> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}`)
  return parseJsonResponse<DocumentPayload>(response)
}

export async function completeSentence(projectId: string, sentenceId: string, completed = true) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ completed }),
  })
  if (!response.ok) throw new Error(await responseMessage(response))
  return response.json() as Promise<{ completed: boolean }>
}

export function documentExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.jsonl`
}
