import { ref, type ComputedRef, type Ref } from 'vue'
import {
  acceptSuggestion,
  acceptSentenceSuggestions,
  applyDocumentSuggestionReviews,
  applySentenceSuggestionReviews,
  autoAnnotateSuggestions,
  autoAcceptSuggestions,
  autoRejectSuggestions,
  generateSentenceSuggestions,
  generateSuggestions,
  rejectSuggestion,
  rejectSentenceSuggestions,
  reviewSentenceSuggestions,
  reviewSuggestion,
} from '../api/suggestions'
import {
  PROJECT_ID,
  type AnnotationDef,
  type DocumentMeta,
  type Metrics,
  type SentenceDef,
  type SuggestionDef,
  type SuggestionReview,
} from '../types/domain'

type UseReaderSuggestionsOptions = {
  activeSuggestions: ComputedRef<SuggestionDef[]>
  currentSentence: ComputedRef<SentenceDef | null>
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  isSaving: Ref<boolean>
  isSuggesting: Ref<boolean>
  loadDocument: (documentId: string, preserveCurrent?: boolean) => Promise<void>
  loadSentenceWindow: (documentId: string, targetIndex: number, force?: boolean) => Promise<void>
  metrics: Ref<Metrics>
  readerError: Ref<string>
  refreshAuditSummary: () => Promise<void>
  refreshDocumentSummary: () => Promise<void>
  refreshRunHistory: () => Promise<void>
  replaceSentenceAnnotations: (sentenceId: string, annotations: AnnotationDef[]) => void
  removeSuggestion: (suggestionId: string) => void
  jumpToNextReviewIfCurrentCleared: () => void
}

export function useReaderSuggestions(options: UseReaderSuggestionsOptions) {
  const suggestionLimit = ref(6)
  const suggestionMinConfidence = ref(0.7)
  const suggestionReviews = ref<Record<string, SuggestionReview>>({})
  const reviewingSuggestionId = ref('')

  async function generateDocumentSuggestions() {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.isSuggesting.value) return
    options.isSuggesting.value = true
    options.readerError.value = ''
    try {
      await generateSuggestions(PROJECT_ID, documentId, suggestionLimit.value, suggestionMinConfidence.value)
      await options.loadDocument(documentId, true)
      await options.refreshRunHistory()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not generate suggestions.'
    } finally {
      options.isSuggesting.value = false
    }
  }

  async function generateCurrentSentenceSuggestions() {
    const documentId = options.documentMeta.value?.id
    const sentence = options.currentSentence.value
    if (!documentId || !sentence || options.isSuggesting.value) return
    options.isSuggesting.value = true
    options.readerError.value = ''
    try {
      await generateSentenceSuggestions(PROJECT_ID, documentId, sentence.id, suggestionLimit.value, suggestionMinConfidence.value)
      await options.refreshDocumentSummary()
      await options.loadSentenceWindow(documentId, options.currentSentenceIndex.value, true)
      await options.refreshRunHistory()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not generate current sentence suggestions.'
    } finally {
      options.isSuggesting.value = false
    }
  }

  function setSuggestionLimit(value: number) {
    const rounded = Math.round(Number.isFinite(value) ? value : suggestionLimit.value)
    suggestionLimit.value = Math.min(Math.max(rounded, 1), 20)
  }

  function setSuggestionMinConfidence(value: number) {
    const numericValue = Number.isFinite(value) ? value : suggestionMinConfidence.value
    suggestionMinConfidence.value = Math.min(Math.max(numericValue, 0), 1)
  }

  async function acceptSuggestedSpan(suggestion: SuggestionDef) {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await acceptSuggestion(PROJECT_ID, suggestion.id)
      options.replaceSentenceAnnotations(suggestion.sentence_id, payload.annotations)
      options.removeSuggestion(suggestion.id)
      await options.refreshDocumentSummary()
      await options.refreshAuditSummary()
      options.jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not accept suggestion.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function acceptCurrentSentenceSuggestions() {
    const sentence = options.currentSentence.value
    if (!sentence || !options.activeSuggestions.value.length || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await acceptSentenceSuggestions(PROJECT_ID, sentence.id)
      options.replaceSentenceAnnotations(sentence.id, payload.annotations)
      await options.refreshDocumentSummary()
      if (options.documentMeta.value) await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      await options.refreshAuditSummary()
      options.jumpToNextReviewIfCurrentCleared()
      if (payload.accepted === 0 && payload.skipped > 0) {
        options.readerError.value = 'Current suggestions overlap existing annotations and were skipped.'
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not accept current suggestions.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function autoAcceptDocumentSuggestions() {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.metrics.value.suggestion_count === 0 || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const result = await autoAcceptSuggestions(PROJECT_ID, documentId, suggestionMinConfidence.value)
      await options.loadDocument(documentId, true)
      if (result.accepted === 0) {
        options.readerError.value = `No suggestions met the ${Math.round(result.min_confidence * 100)}% threshold.`
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not auto-accept suggestions.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function autoAnnotateDocument() {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.isSaving.value || options.isSuggesting.value) return
    options.isSaving.value = true
    options.isSuggesting.value = true
    options.readerError.value = ''
    try {
      const result = await autoAnnotateSuggestions(PROJECT_ID, documentId, suggestionLimit.value, suggestionMinConfidence.value)
      await options.loadDocument(documentId, true)
      await options.refreshRunHistory()
      await options.refreshAuditSummary()
      if (result.accepted === 0) {
        options.readerError.value = `No character RAG spans met the ${Math.round(result.min_confidence * 100)}% threshold.`
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not auto-annotate document.'
    } finally {
      options.isSaving.value = false
      options.isSuggesting.value = false
    }
  }

  async function rejectSuggestedSpan(suggestion: SuggestionDef) {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      await rejectSuggestion(PROJECT_ID, suggestion.id)
      options.removeSuggestion(suggestion.id)
      await options.refreshDocumentSummary()
      await options.refreshAuditSummary()
      options.jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not reject suggestion.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function rejectCurrentSentenceSuggestions() {
    const sentence = options.currentSentence.value
    if (!sentence || !options.activeSuggestions.value.length || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      await rejectSentenceSuggestions(PROJECT_ID, sentence.id)
      await options.refreshDocumentSummary()
      if (options.documentMeta.value) await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      await options.refreshAuditSummary()
      options.jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not reject current suggestions.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function autoRejectDocumentSuggestions() {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.metrics.value.suggestion_count === 0 || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const result = await autoRejectSuggestions(PROJECT_ID, documentId)
      await options.loadDocument(documentId, true)
      if (result.rejected === 0) {
        options.readerError.value = 'No pending suggestions to reject.'
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not auto-reject suggestions.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function reviewSuggestedSpan(suggestion: SuggestionDef) {
    if (reviewingSuggestionId.value) return
    reviewingSuggestionId.value = suggestion.id
    options.readerError.value = ''
    try {
      const review = await reviewSuggestion(PROJECT_ID, suggestion.id)
      suggestionReviews.value = { ...suggestionReviews.value, [suggestion.id]: review }
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not review suggestion with LLM.'
    } finally {
      reviewingSuggestionId.value = ''
    }
  }

  async function reviewCurrentSentenceSuggestions() {
    const sentence = options.currentSentence.value
    if (!sentence || !options.activeSuggestions.value.length || options.isSaving.value || reviewingSuggestionId.value) return
    options.isSaving.value = true
    reviewingSuggestionId.value = `sentence:${sentence.id}`
    options.readerError.value = ''
    try {
      const payload = await reviewSentenceSuggestions(PROJECT_ID, sentence.id)
      const nextReviews = { ...suggestionReviews.value }
      for (const review of payload.reviews) nextReviews[review.suggestion_id] = review
      suggestionReviews.value = nextReviews
      await options.refreshDocumentSummary()
      if (options.documentMeta.value) await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      await options.refreshAuditSummary()
      if (payload.reviewed === 0) {
        options.readerError.value = 'No pending suggestions to review in this sentence.'
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not review current suggestions with LLM.'
    } finally {
      options.isSaving.value = false
      reviewingSuggestionId.value = ''
    }
  }

  async function applyCurrentSentenceSuggestionReviews() {
    const sentence = options.currentSentence.value
    if (!sentence || !options.activeSuggestions.value.length || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await applySentenceSuggestionReviews(PROJECT_ID, sentence.id)
      options.replaceSentenceAnnotations(sentence.id, payload.annotations)
      await options.refreshDocumentSummary()
      if (options.documentMeta.value) await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      await options.refreshAuditSummary()
      options.jumpToNextReviewIfCurrentCleared()
      if (payload.accepted === 0 && payload.rejected === 0) {
        options.readerError.value = 'No LLM accept/reject recommendations to apply in this sentence.'
      } else if (payload.skipped > 0) {
        options.readerError.value = `${payload.skipped} reviewed suggestions overlapped existing annotations and were skipped.`
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not apply LLM review recommendations.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function applyDocumentSuggestionReviewsFromLlm() {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.metrics.value.reviewed_suggestion_count === 0 || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await applyDocumentSuggestionReviews(PROJECT_ID, documentId)
      await options.loadDocument(documentId, true)
      await options.refreshAuditSummary()
      if (payload.accepted === 0 && payload.rejected === 0) {
        options.readerError.value = 'No LLM accept/reject recommendations to apply in this document.'
      } else if (payload.skipped > 0) {
        options.readerError.value = `${payload.skipped} reviewed suggestions overlapped existing annotations and were skipped.`
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not apply document LLM review recommendations.'
    } finally {
      options.isSaving.value = false
    }
  }

  function restoreSuggestionReviews(sourceSentences: SentenceDef[]) {
    const restored: Record<string, SuggestionReview> = {}
    for (const sentence of sourceSentences) {
      for (const suggestion of sentence.suggestions) {
        if (!suggestion.latest_review) continue
        restored[suggestion.id] = { suggestion_id: suggestion.id, ...suggestion.latest_review }
      }
    }
    suggestionReviews.value = restored
  }

  function resetSuggestionState() {
    suggestionReviews.value = {}
    reviewingSuggestionId.value = ''
  }

  return {
    acceptCurrentSentenceSuggestions,
    acceptSuggestedSpan,
    applyCurrentSentenceSuggestionReviews,
    applyDocumentSuggestionReviewsFromLlm,
    autoAcceptDocumentSuggestions,
    autoAnnotateDocument,
    autoRejectDocumentSuggestions,
    generateCurrentSentenceSuggestions,
    generateDocumentSuggestions,
    rejectCurrentSentenceSuggestions,
    rejectSuggestedSpan,
    resetSuggestionState,
    restoreSuggestionReviews,
    reviewCurrentSentenceSuggestions,
    reviewingSuggestionId,
    reviewSuggestedSpan,
    setSuggestionLimit,
    setSuggestionMinConfidence,
    suggestionLimit,
    suggestionMinConfidence,
    suggestionReviews,
  }
}
