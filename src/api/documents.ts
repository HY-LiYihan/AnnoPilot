import type {
  DocumentListPayload,
  DocumentPayload,
  DocumentSummaryPayload,
  ImportAnnotationsResponse,
  ImportTxtResponse,
  SentencesPagePayload,
} from '../types/domain'
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

export async function importAnnotationsJsonl(projectId: string, documentId: string, file: File): Promise<ImportAnnotationsResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/import-annotations-jsonl`, {
    method: 'POST',
    body: formData,
  })
  return parseJsonResponse<ImportAnnotationsResponse>(response)
}

export async function fetchDocument(projectId: string, documentId: string): Promise<DocumentPayload> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}`)
  return parseJsonResponse<DocumentPayload>(response)
}

export async function fetchDocuments(projectId: string, limit = 50): Promise<DocumentListPayload> {
  const params = new URLSearchParams({ limit: String(limit) })
  const response = await fetch(`/api/projects/${projectId}/documents?${params.toString()}`)
  return parseJsonResponse<DocumentListPayload>(response)
}

export async function fetchDocumentSummary(projectId: string, documentId: string): Promise<DocumentSummaryPayload> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/summary`)
  return parseJsonResponse<DocumentSummaryPayload>(response)
}

export async function fetchDocumentSentences(
  projectId: string,
  documentId: string,
  offset = 0,
  limit = 50,
): Promise<SentencesPagePayload> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/sentences?${params.toString()}`)
  return parseJsonResponse<SentencesPagePayload>(response)
}

export async function completeSentence(projectId: string, sentenceId: string, completed = true, answer?: 'accept' | 'reject' | 'ignore' | 'pending') {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ completed, answer }),
  })
  if (!response.ok) throw new Error(await responseMessage(response))
  return response.json() as Promise<{ completed: boolean; answer: string }>
}

export function documentExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.jsonl`
}

export function prodigyExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.prodigy.jsonl`
}

export function manifestExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.manifest.json`
}

export function eventsExportUrl(projectId: string) {
  return `/api/projects/${projectId}/events.jsonl`
}

export function tagSchemaExportUrl(projectId: string) {
  return `/api/projects/${projectId}/tags/schema.json`
}
