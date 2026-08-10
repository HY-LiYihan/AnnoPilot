import type { AnnotationDef, SuggestionDef, SuggestionReview } from '../types/domain'
import { parseJsonResponse } from './http'

export async function generateSuggestions(projectId: string, documentId: string, limitPerSentence = 6, minConfidence = 0) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/suggestions/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_per_sentence: limitPerSentence, min_confidence: minConfidence }),
  })
  return parseJsonResponse<{
    run_id: string
    suggestions_created: number
    source_counts: Record<string, number>
    confidence_counts: Record<string, number>
    suggestions: SuggestionDef[]
  }>(response)
}

export async function generateSentenceSuggestions(
  projectId: string,
  documentId: string,
  sentenceId: string,
  limitPerSentence = 6,
  minConfidence = 0,
) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/sentences/${sentenceId}/suggestions/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_per_sentence: limitPerSentence, min_confidence: minConfidence }),
  })
  return parseJsonResponse<{
    run_id: string
    suggestions_created: number
    source_counts: Record<string, number>
    confidence_counts: Record<string, number>
    suggestions: SuggestionDef[]
  }>(response)
}

export async function acceptSuggestion(projectId: string, suggestionId: string) {
  const response = await fetch(`/api/projects/${projectId}/suggestions/${suggestionId}/accept`, { method: 'POST' })
  return parseJsonResponse<{ accepted: boolean; annotations: AnnotationDef[] }>(response)
}

export async function acceptSentenceSuggestions(projectId: string, sentenceId: string) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/suggestions/accept`, { method: 'POST' })
  return parseJsonResponse<{
    accepted: number
    skipped: number
    accepted_suggestion_ids: string[]
    affected_sentence_ids: string[]
    annotations: AnnotationDef[]
  }>(response)
}

export async function applySentenceSuggestionReviews(projectId: string, sentenceId: string) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/suggestions/apply-llm-review`, { method: 'POST' })
  return parseJsonResponse<{
    accepted: number
    rejected: number
    skipped: number
    kept: number
    accepted_suggestion_ids: string[]
    rejected_suggestion_ids: string[]
    affected_sentence_ids: string[]
    annotations: AnnotationDef[]
  }>(response)
}

export async function applyDocumentSuggestionReviews(projectId: string, documentId: string) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/suggestions/apply-llm-review`, { method: 'POST' })
  return parseJsonResponse<{
    accepted: number
    rejected: number
    skipped: number
    kept: number
    accepted_suggestion_ids: string[]
    rejected_suggestion_ids: string[]
    affected_sentence_ids: string[]
  }>(response)
}

export async function autoAcceptSuggestions(projectId: string, documentId: string, minConfidence = 0.9) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/suggestions/auto-accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ min_confidence: minConfidence }),
  })
  return parseJsonResponse<{
    accepted: number
    skipped: number
    min_confidence: number
    accepted_suggestion_ids: string[]
    affected_sentence_ids: string[]
  }>(response)
}

export async function autoAnnotateSuggestions(projectId: string, documentId: string, limitPerSentence = 6, minConfidence = 0.9) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/suggestions/auto-annotate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit_per_sentence: limitPerSentence, min_confidence: minConfidence }),
  })
  return parseJsonResponse<{
    run_id: string
    suggestions_created: number
    source_counts: Record<string, number>
    confidence_counts: Record<string, number>
    accepted: number
    skipped: number
    min_confidence: number
    accepted_suggestion_ids: string[]
    affected_sentence_ids: string[]
  }>(response)
}

export async function rejectSuggestion(projectId: string, suggestionId: string) {
  const response = await fetch(`/api/projects/${projectId}/suggestions/${suggestionId}/reject`, { method: 'POST' })
  return parseJsonResponse<{ rejected: boolean; suggestion_id: string }>(response)
}

export async function rejectSentenceSuggestions(projectId: string, sentenceId: string) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/suggestions/reject`, { method: 'POST' })
  return parseJsonResponse<{
    rejected: number
    rejected_suggestion_ids: string[]
    affected_sentence_ids: string[]
  }>(response)
}

export async function autoRejectSuggestions(projectId: string, documentId: string) {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/suggestions/auto-reject`, { method: 'POST' })
  return parseJsonResponse<{
    rejected: number
    rejected_suggestion_ids: string[]
    affected_sentence_ids: string[]
  }>(response)
}

export async function reviewSuggestion(projectId: string, suggestionId: string) {
  const response = await fetch(`/api/projects/${projectId}/suggestions/${suggestionId}/llm-review`, { method: 'POST' })
  return parseJsonResponse<SuggestionReview>(response)
}

export async function reviewSentenceSuggestions(projectId: string, sentenceId: string) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/suggestions/llm-review`, { method: 'POST' })
  return parseJsonResponse<{
    reviewed: number
    reviewed_suggestion_ids: string[]
    reviews: SuggestionReview[]
  }>(response)
}
