import type {
  AutoMarkMonoglossResponse,
  DocumentListPayload,
  DocumentPayload,
  DocumentSummaryPayload,
  ImportAnnotationsResponse,
  ImportTxtResponse,
  LoadSamplePresetResponse,
  ProjectResetResponse,
  ReviewQueuePayload,
  ReviewQueueOrder,
  SamplePresetListPayload,
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

export async function mergeTxt(projectId: string, documentId: string, file: File): Promise<ImportTxtResponse> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/merge-txt`, {
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

export async function resetProject(projectId: string): Promise<ProjectResetResponse> {
  const response = await fetch(`/api/projects/${projectId}/reset`, {
    method: 'POST',
  })
  return parseJsonResponse<ProjectResetResponse>(response)
}

export async function fetchSamplePresets(projectId: string): Promise<SamplePresetListPayload> {
  const response = await fetch(`/api/projects/${projectId}/sample-presets`)
  return parseJsonResponse<SamplePresetListPayload>(response)
}

type LoadSamplePresetOptions = {
  autoAcceptSuggestions?: boolean
  completeSentences?: boolean
}

export async function loadSamplePreset(
  projectId: string,
  presetId: string,
  options: LoadSamplePresetOptions = {},
): Promise<LoadSamplePresetResponse> {
  const response = await fetch(`/api/projects/${projectId}/sample-presets/${presetId}/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      generate_suggestions: true,
      auto_accept_suggestions: Boolean(options.autoAcceptSuggestions),
      complete_sentences: Boolean(options.completeSentences),
    }),
  })
  return parseJsonResponse<LoadSamplePresetResponse>(response)
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

export async function fetchReviewQueue(projectId: string, documentId: string, limit = 20, order: ReviewQueueOrder = 'position'): Promise<ReviewQueuePayload> {
  const params = new URLSearchParams({ limit: String(limit), order })
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/review-queue?${params.toString()}`)
  return parseJsonResponse<ReviewQueuePayload>(response)
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

export async function autoMarkDocumentMonogloss(projectId: string, documentId: string): Promise<AutoMarkMonoglossResponse> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/monogloss/auto-mark`, {
    method: 'POST',
  })
  return parseJsonResponse<AutoMarkMonoglossResponse>(response)
}

export async function updateDocumentCursor(projectId: string, documentId: string, currentSentenceIndex: number) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/session/cursor`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_sentence_index: currentSentenceIndex }),
  })
  if (!response.ok) throw new Error(await responseMessage(response))
  return response.json() as Promise<{ session: { current_sentence_index: number; updated_at: string } }>
}

export function documentExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.jsonl`
}

export function prodigyExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.prodigy.jsonl`
}

export function prodigySpansExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.prodigy.spans.jsonl`
}

export function prodigyBundleExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.prodigy.bundle.zip`
}

export function prodigyLabelsExportUrl(projectId: string) {
  return `/api/projects/${projectId}/tags/prodigy-labels.json`
}

export function goldsmithReviewQueueExportUrl(projectId: string, documentId: string, order: ReviewQueueOrder = 'hybrid') {
  const params = new URLSearchParams({ order, limit: '100' })
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.review-queue.jsonl?${params.toString()}`
}

export function goldsmithHumanChoicesExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.human-choices.jsonl`
}

export function goldsmithHardExamplesExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.hard-examples.jsonl`
}

export function goldsmithBoundaryFeedbackExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.boundary-feedback.jsonl`
}

export function goldsmithConsistencyScoresExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.consistency-scores.jsonl`
}

export function goldsmithCandidateRunsExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.candidate-runs.jsonl`
}

export function goldsmithRiskReasonsExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.risk-reasons.jsonl`
}

export function goldsmithReviewTasksExportUrl(projectId: string, documentId: string) {
  return `/api/projects/${projectId}/documents/${documentId}/export.goldsmith.review-tasks.jsonl`
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
