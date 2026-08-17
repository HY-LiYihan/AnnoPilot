import { computed, ref } from 'vue'
import {
  PROJECT_ID,
  type AnnotationDef,
  type AssistanceErrorReason,
  type DocumentMeta,
  type DocumentListItem,
  type DocumentSummaryPayload,
  type Metrics,
  type SamplePreset,
  type SentenceDef,
  type SentenceQueueItem,
  type SessionState,
  type SuggestionDef,
} from '../types/domain'
import { useTokenSelection } from './useTokenSelection'
import { useReaderKeyboardShortcuts } from './useReaderKeyboardShortcuts'
import { useReaderExports } from './useReaderExports'
import { useReaderTags } from './useReaderTags'
import { useReaderAudit } from './useReaderAudit'
import { useReaderSuggestions } from './useReaderSuggestions'
import { useReaderAnnotationActions } from './useReaderAnnotationActions'
import { useReaderSentenceWindow } from './useReaderSentenceWindow'
import { useReaderReviewQueue } from './useReaderReviewQueue'
import { useReaderSentenceCompletion } from './useReaderSentenceCompletion'
import { useReaderAssistance, type LocalAssistanceDraftSpan } from './useReaderAssistance'
import { emptyMetrics, useReaderDocumentLifecycle } from './useReaderDocumentLifecycle'
import { sliceByCodePoint } from '../utils/unicode'
import {
  annotationForToken as findAnnotationForToken,
  suggestionForToken as findSuggestionForToken,
  suggestionsWithoutAnnotationOverlaps,
  tokenPrefix as getTokenPrefix,
  tokenStyleForToken,
} from './readerTokenDisplay'

type SentenceAnswer = 'accept' | 'reject' | 'ignore'

export function useDocumentReader() {
  const samplePresets = ref<SamplePreset[]>([])
  const documents = ref<DocumentListItem[]>([])
  const documentMeta = ref<DocumentMeta | null>(null)
  const activeSession = ref<SessionState | null>(null)
  const sentences = ref<SentenceDef[]>([])
  const sentenceQueue = ref<SentenceQueueItem[]>([])
  const loadedWindow = ref({ offset: 0, limit: 0, total: 0 })
  const metrics = ref<Metrics>(emptyMetrics())
  const currentSentenceIndex = ref(0)
  const isUploading = ref(false)
  const isSaving = ref(false)
  const isSuggesting = ref(false)
  const isResetting = ref(false)
  const readerError = ref('')
  const activeSuggestionId = ref('')
  const sentenceElements = ref<Record<string, HTMLElement | null>>({})

  const selection = useTokenSelection(sentences)
  let applyDocumentSummaryImpl: (payload: DocumentSummaryPayload) => void = () => {}
  let loadDocumentImpl: (documentId: string, preserveCurrent?: boolean) => Promise<void> = async () => {}
  let loadDocumentListImpl: () => Promise<void> = async () => {}
  let refreshDocumentSummaryImpl: () => Promise<void> = async () => {}
  let restoreSuggestionReviewsForLoadedSentences = (_sentences: SentenceDef[]) => {}

  const applyDocumentSummary = (payload: DocumentSummaryPayload) => applyDocumentSummaryImpl(payload)
  const loadDocument = (documentId: string, preserveCurrent = false) => loadDocumentImpl(documentId, preserveCurrent)
  const loadDocumentList = () => loadDocumentListImpl()
  const refreshDocumentSummary = () => refreshDocumentSummaryImpl()

  const {
    centerCurrentSentence,
    clampIndex,
    currentSentence,
    loadSentenceWindow,
    onSentenceClick,
    persistSessionCursor,
    setCurrentSentence,
    setSentenceElement,
  } = useReaderSentenceWindow({
    activeSession,
    activeSuggestionId,
    currentSentenceIndex,
    documentMeta,
    documents,
    loadedWindow,
    metrics,
    normalizeActiveSuggestionTarget,
    onSentencesLoaded: (loadedSentences) => restoreSuggestionReviewsForLoadedSentences(loadedSentences),
    selection,
    sentenceElements,
    sentenceQueue,
    sentences,
  })

  const {
    auditSummary,
    handleAnnotationImport,
    isVerifyingRebuild,
    lastAnnotationImport,
    rebuildPreview,
    refreshAnnotationImportHistory,
    refreshAuditSummary,
    refreshRunHistory,
    resetAuditState,
    runHistory,
    verifyRebuildPreview,
  } = useReaderAudit({
    documentMeta,
    isUploading,
    loadDocument,
    readerError,
  })

  const {
    addTag,
    deleteTag,
    findMonoglossTag,
    handleTagSchemaImport,
    loadProjectTags,
    renameTag,
    selectedTag,
    selectedTagId,
    setTags,
    tags,
  } = useReaderTags({
    currentSentenceIndex,
    documentMeta,
    isSaving,
    loadDocument,
    loadSentenceWindow,
    readerError,
    refreshAuditSummary,
    refreshDocumentSummary,
  })

  const progressPercent = computed(() => Math.min(Math.max(metrics.value.progress * 100, 0), 100))
  const reviewedSummary = computed(() => `${metrics.value.completed_count} / ${metrics.value.sentence_count || 0}`)
  const reviewSummary = computed(() => `${metrics.value.suggestion_count} 待确认`)
  const activeAnnotations = computed(() => currentSentence.value?.annotations ?? [])
  const activeSuggestions = computed(() => currentSentence.value?.suggestions ?? [])
  const activeSuggestion = computed(() => activeSuggestions.value.find((suggestion) => suggestion.id === activeSuggestionId.value) ?? activeSuggestions.value[0] ?? null)
  const activeSuggestionTargetId = computed(() => activeSuggestion.value?.id ?? '')
  const activeSuggestionPosition = computed(() => {
    const index = activeSuggestions.value.findIndex((suggestion) => suggestion.id === activeSuggestionTargetId.value)
    return index >= 0 ? index + 1 : 0
  })
  const {
    hasReviewQueue,
    jumpToNextReviewIfCurrentCleared,
    jumpToNextReviewSentence,
    queueItems,
    refreshReviewQueue,
    resetReviewQueueState,
    reviewQueueDetails,
    reviewQueueOrder,
    reviewQueueRouteCounts,
    reviewQueueSummary,
    reviewQueueTotal,
    setReviewQueueOrder,
  } = useReaderReviewQueue({
    activeSuggestions,
    currentSentenceIndex,
    documentMeta,
    sentenceQueue,
    setCurrentSentence,
  })

  const {
    completeCurrentSentence: completeManualSentence,
    reopenCurrentSentence,
  } = useReaderSentenceCompletion({
    applyDocumentSummary,
    currentSentence,
    currentSentenceIndex,
    documentMeta,
    documents,
    isSaving,
    loadDocumentList,
    metrics,
    readerError,
    refreshAuditSummary,
    refreshDocumentSummary,
    refreshReviewQueue,
    sentenceQueue,
    sentences,
    setCurrentSentence,
  })

  const assistanceDocumentId = computed(() => documentMeta.value?.id ?? null)
  const assistanceSentenceId = computed(() => currentSentence.value?.id ?? null)
  const {
    status: assistanceStatus,
    error: assistanceError,
    isDeciding: isAssistanceDeciding,
    currentDraft: currentAssistanceDraft,
    localDraftSpans: assistanceDraftSpans,
    errorReasons: assistanceErrorReasons,
    isDraftModified: isAssistanceDraftModified,
    tagProgress: assistanceTagProgress,
    setLocalDraftTokenRange,
    removeLocalDraftSpan,
    refresh: refreshAssistance,
    confirmDraft: confirmAssistanceDraft,
    correctDraft: correctAssistanceDraft,
    skipDraft: skipAssistanceDraft,
  } = useReaderAssistance({
    projectId: PROJECT_ID,
    documentId: assistanceDocumentId,
    currentSentenceId: assistanceSentenceId,
    onDecision: handleAssistanceDecision,
  })
  const assistanceDraftActive = computed(() => Boolean(currentAssistanceDraft.value))

  async function completeCurrentSentence(answer: SentenceAnswer = 'accept') {
    if (!currentAssistanceDraft.value) return completeManualSentence(answer)
    if (answer !== 'accept') return skipCurrentAssistanceDraft()
    readerError.value = ''
    try {
      if (isAssistanceDraftModified.value) {
        await correctAssistanceDraft()
      } else {
        await confirmAssistanceDraft()
      }
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not confirm assistance draft.'
    }
  }

  async function skipCurrentAssistanceDraft() {
    if (!currentAssistanceDraft.value || isAssistanceDeciding.value) return
    readerError.value = ''
    try {
      await skipAssistanceDraft()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not skip assistance draft.'
    }
  }

  async function handleAssistanceDecision(target: { sentenceIndex: number | null } | null) {
    const documentId = documentMeta.value?.id
    const fallbackIndex = Math.min(currentSentenceIndex.value + 1, Math.max(metrics.value.sentence_count - 1, 0))
    const nextIndex = target?.sentenceIndex ?? fallbackIndex
    await refreshDocumentSummary()
    if (documentId) await loadSentenceWindow(documentId, nextIndex, true)
    setCurrentSentence(nextIndex, 'auto')
    await refreshAuditSummary()
  }

  const {
    applyTagToSelection: applyManualTagToSelection,
    autoMarkEmptySentencesMonogloss,
    canUndoSpanAction,
    handleTagClick: handleManualTagClick,
    markCurrentSentenceMonogloss,
    removeAnnotation: removeManualAnnotation,
    removeAnnotations: removeManualAnnotations,
    resetAnnotationActionState,
    selectCurrentSentenceSpan,
    undoLabel,
    undoLastSpanAction,
  } = useReaderAnnotationActions({
    completeCurrentSentence,
    currentSentence,
    currentSentenceIndex,
    documentMeta,
    findMonoglossTag,
    isSaving,
    loadSentenceWindow,
    readerError,
    refreshAuditSummary,
    refreshDocumentSummary,
    replaceSentenceAnnotations,
    selectedTagId,
    selection,
    sentences,
    tags,
  })

  const activeAssistanceAnnotations = computed<AnnotationDef[]>(() => {
    const sentence = currentSentence.value
    const draft = currentAssistanceDraft.value
    if (!sentence || !draft) return []
    return assistanceDraftSpans.value.flatMap((span, index) => {
      const startToken = sentence.tokens.find((token) => token.token_index === span.start_token_index)
      const endToken = sentence.tokens.find((token) => token.token_index === span.end_token_index)
      const tag = tags.value.find((item) => item.id === span.tag_id)
      if (!startToken || !endToken || !tag) return []
      return [{
        id: assistanceAnnotationId(draft.draft_id, span, index),
        tag_id: tag.id,
        tag_name: tag.name,
        tag_color: tag.color,
        start_token_index: span.start_token_index,
        end_token_index: span.end_token_index,
        start_char: startToken.start_char,
        end_char: endToken.end_char,
        text: sliceByCodePoint(
          sentence.text,
          startToken.start_char - sentence.start_char,
          endToken.end_char - sentence.start_char,
        ),
        source: 'assistance_draft',
        source_suggestion_id: span.suggestion_id ?? null,
        created_at: '',
      }]
    })
  })

  async function applyTagToSelection(tagId: string) {
    const pending = selection.pendingSelection.value
    if (currentAssistanceDraft.value && pending && pending.sentenceId === currentSentence.value?.id) {
      selectedTagId.value = tagId
      setLocalDraftTokenRange(tagId, pending.start, pending.end)
      selection.pendingSelection.value = null
      return
    }
    await applyManualTagToSelection(tagId)
  }

  function handleTagClick(tagId: string) {
    if (currentAssistanceDraft.value) {
      void applyTagToSelection(tagId)
      return
    }
    handleManualTagClick(tagId)
  }

  async function removeAnnotation(annotationId: string) {
    const draftIndex = activeAssistanceAnnotations.value.findIndex((annotation) => annotation.id === annotationId)
    if (currentAssistanceDraft.value && draftIndex >= 0) {
      removeLocalDraftSpan(draftIndex)
      return
    }
    await removeManualAnnotation(annotationId)
  }

  async function removeAnnotations(annotationIds: string[]) {
    if (currentAssistanceDraft.value) {
      const draftIndexes = annotationIds
        .map((annotationId) => activeAssistanceAnnotations.value.findIndex((annotation) => annotation.id === annotationId))
        .filter((index) => index >= 0)
        .sort((left, right) => right - left)
      if (draftIndexes.length) {
        draftIndexes.forEach((index) => removeLocalDraftSpan(index))
        return
      }
    }
    await removeManualAnnotations(annotationIds)
  }

  function toggleAssistanceErrorReason(reason: AssistanceErrorReason) {
    assistanceErrorReasons.value = assistanceErrorReasons.value.includes(reason)
      ? assistanceErrorReasons.value.filter((item) => item !== reason)
      : [...assistanceErrorReasons.value, reason]
  }

  const {
    acceptCurrentSentenceSuggestions,
    acceptSuggestedSpan,
    applyCurrentSentenceSuggestionReviews,
    applyDocumentSuggestionReviewsFromLlm,
    autoAcceptDocumentSuggestions,
    autoAnnotateDocument,
    autoRejectDocumentSuggestions,
    generateCurrentSentenceSuggestions,
    generateEngagementCandidatesForCurrentSentence,
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
    engagementCandidateCount,
    engagementTemperature,
    lastEngagementRun,
    setEngagementCandidateCount,
    setEngagementTemperature,
  } = useReaderSuggestions({
    activeSuggestions,
    currentSentence,
    currentSentenceIndex,
    documentMeta,
    isSaving,
    isSuggesting,
    jumpToNextReviewIfCurrentCleared,
    loadDocument,
    loadSentenceWindow,
    metrics,
    readerError,
    refreshAuditSummary,
    refreshDocumentSummary,
    refreshRunHistory,
    removeSuggestion,
    replaceSentenceAnnotations,
  })
  restoreSuggestionReviewsForLoadedSentences = restoreSuggestionReviews

  const readerDocuments = useReaderDocumentLifecycle({
    activeSession,
    activeSuggestionId,
    centerCurrentSentence,
    clampIndex,
    currentSentenceIndex,
    documentMeta,
    documents,
    isResetting,
    isSuggesting,
    isUploading,
    lastAnnotationImport,
    loadedWindow,
    loadProjectTags,
    loadSentenceWindow,
    metrics,
    persistSessionCursor,
    readerError,
    refreshAnnotationImportHistory,
    refreshAuditSummary,
    refreshReviewQueue,
    refreshRunHistory,
    resetAnnotationActionState,
    resetAuditState,
    resetReviewQueueState,
    resetSuggestionState,
    samplePresets,
    selection,
    sentenceElements,
    sentenceQueue,
    sentences,
    setTags,
  })
  applyDocumentSummaryImpl = readerDocuments.applyDocumentSummary
  loadDocumentImpl = readerDocuments.loadDocument
  loadDocumentListImpl = readerDocuments.loadDocumentList
  refreshDocumentSummaryImpl = readerDocuments.refreshDocumentSummary

  const readerExports = useReaderExports({
    documentMeta,
    reviewQueueOrder,
    onEventsExport: refreshAuditSummary,
  })
  const {
    exportEventsJsonl,
    exportGoldsmithBootstrapReportMd,
    exportGoldsmithBoundaryFeedbackJsonl,
    exportGoldsmithCandidateRunsJsonl,
    exportGoldsmithConsistencyScoresJsonl,
    exportGoldsmithContrastiveExamplesJsonl,
    exportGoldsmithHardExamplesJsonl,
    exportGoldsmithHumanChoicesJsonl,
    exportGoldsmithLabelStatisticsJsonl,
    exportGoldsmithPromptPackageJsonl,
    exportGoldsmithReflectionPlansJsonl,
    exportGoldsmithRiskReasonsJsonl,
    exportGoldsmithReviewTasksJsonl,
    exportGoldsmithReviewQueueJsonl,
    exportGoldsmithVerificationReportJsonl,
    exportJsonl,
    exportManifestJson,
    exportProdigyBundleZip,
    exportProdigyLabelsJson,
    exportProdigyJsonl,
    exportProdigySpansJsonl,
    exportRunProvenanceJson,
    exportTagSchemaJson,
  } = readerExports

  useReaderKeyboardShortcuts({
    assistanceDraftActive,
    acceptCurrentSentenceSuggestions,
    acceptSuggestedSpan,
    activeSuggestion,
    activeSuggestions,
    applyTagToSelection,
    completeCurrentSentence,
    cycleActiveSuggestionTarget,
    currentSentenceIndex,
    jumpToNextReviewSentence,
    markCurrentSentenceMonogloss,
    rejectCurrentSentenceSuggestions,
    rejectSuggestedSpan,
    reopenCurrentSentence,
    selectCurrentSentenceSpan,
    setCurrentSentence,
    tags,
    undoLastSpanAction,
  })

  function setActiveSuggestionTarget(suggestion: SuggestionDef) {
    if (!activeSuggestions.value.some((item) => item.id === suggestion.id)) return
    activeSuggestionId.value = suggestion.id
  }

  function cycleActiveSuggestionTarget(direction: 1 | -1) {
    const suggestions = activeSuggestions.value
    if (!suggestions.length) return
    const currentIndex = suggestions.findIndex((suggestion) => suggestion.id === activeSuggestionTargetId.value)
    const safeIndex = currentIndex >= 0 ? currentIndex : 0
    const nextIndex = (safeIndex + direction + suggestions.length) % suggestions.length
    activeSuggestionId.value = suggestions[nextIndex].id
  }

  function normalizeActiveSuggestionTarget() {
    if (!activeSuggestions.value.length) {
      activeSuggestionId.value = ''
      return
    }
    if (!activeSuggestions.value.some((suggestion) => suggestion.id === activeSuggestionId.value)) {
      activeSuggestionId.value = activeSuggestions.value[0].id
    }
  }

  function onTokenPointerDown(sentence: SentenceDef, tokenIndex: number, event: PointerEvent) {
    if (sentence.index !== currentSentenceIndex.value) {
      setCurrentSentence(sentence.index)
      return
    }
    event.preventDefault()
    selection.beginSelection(sentence.id, tokenIndex)
  }

  function onTokenPointerEnter(sentence: SentenceDef, tokenIndex: number) {
    selection.extendSelection(sentence.id, tokenIndex)
  }

  function onTokenPointerUp(sentence: SentenceDef, tokenIndex: number) {
    selection.finishSelection(sentence.id, tokenIndex)
  }

  function replaceSentenceAnnotations(sentenceId: string, annotations: AnnotationDef[]) {
    sentences.value = sentences.value.map((sentence) =>
      sentence.id === sentenceId ? { ...sentence, annotations, suggestions: suggestionsWithoutAnnotationOverlaps(sentence.suggestions, annotations) } : sentence,
    )
    normalizeActiveSuggestionTarget()
  }

  function removeSuggestion(suggestionId: string) {
    sentences.value = sentences.value.map((sentence) => ({
      ...sentence,
      suggestions: sentence.suggestions.filter((suggestion) => suggestion.id !== suggestionId),
    }))
    normalizeActiveSuggestionTarget()
  }

  function annotationForToken(sentence: SentenceDef, tokenIndex: number) {
    if (sentence.id === currentSentence.value?.id && currentAssistanceDraft.value) {
      return activeAssistanceAnnotations.value.find(
        (annotation) => annotation.start_token_index <= tokenIndex && annotation.end_token_index >= tokenIndex,
      )
    }
    return findAnnotationForToken(sentence, tokenIndex)
  }

  function tokenPrefix(sentence: SentenceDef, tokenIndex: number) {
    return getTokenPrefix(sentence, tokenIndex)
  }

  function tokenStyle(sentence: SentenceDef, tokenIndex: number): Record<string, string> {
    const assistanceAnnotation = annotationForToken(sentence, tokenIndex)
    if (currentAssistanceDraft.value && assistanceAnnotation) {
      return { '--token-color': assistanceAnnotation.tag_color }
    }
    return tokenStyleForToken(sentence, tokenIndex, {
      activeSuggestion: activeSuggestion.value,
      selectedTag: selectedTag.value,
      isTokenInDrag: selection.isTokenInDrag,
      isTokenPending: selection.isTokenPending,
    })
  }

  function suggestionForToken(sentence: SentenceDef, tokenIndex: number) {
    if (sentence.id === currentSentence.value?.id && currentAssistanceDraft.value) return undefined
    return findSuggestionForToken(sentence, tokenIndex)
  }

  return {
    tags,
    samplePresets,
    documents,
    documentMeta,
    activeSession,
    sentences,
    metrics,
    selectedTagId,
    currentSentenceIndex,
    suggestionLimit,
    suggestionMinConfidence,
    engagementCandidateCount,
    engagementTemperature,
    lastEngagementRun,
    isUploading,
    isSaving,
    isSuggesting,
    isResetting,
    isVerifyingRebuild,
    readerError,
    auditSummary,
    rebuildPreview,
    runHistory,
    reviewQueueDetails,
    reviewQueueTotal,
    reviewQueueOrder,
    reviewQueueRouteCounts,
    suggestionReviews,
    reviewingSuggestionId,
    activeSuggestionTargetId,
    activeSuggestionPosition,
    lastAnnotationImport,
    currentSentence,
    progressPercent,
    reviewedSummary,
    reviewSummary,
    activeAnnotations,
    activeAssistanceAnnotations,
    activeSuggestions,
    assistanceStatus,
    assistanceError,
    assistanceTagProgress,
    currentAssistanceDraft,
    isAssistanceDeciding,
    isAssistanceDraftModified,
    assistanceErrorReasons,
    reviewQueueSummary,
    canUndoSpanAction,
    undoLabel,
    hasReviewQueue,
    queueItems,
    pendingSelection: selection.pendingSelection,
    pendingSelectionText: selection.pendingSelectionText,
    handleImport: readerDocuments.handleImport,
    loadBuiltinSamplePreset: readerDocuments.loadBuiltinSamplePreset,
    switchDocument: readerDocuments.switchDocument,
    setCurrentSentence,
    jumpToNextReviewSentence,
    setReviewQueueOrder,
    markCurrentSentenceMonogloss,
    autoMarkEmptySentencesMonogloss,
    completeCurrentSentence,
    skipCurrentAssistanceDraft,
    toggleAssistanceErrorReason,
    refreshAssistance,
    reopenCurrentSentence,
    generateDocumentSuggestions,
    generateCurrentSentenceSuggestions,
    generateEngagementCandidatesForCurrentSentence,
    setSuggestionLimit,
    setSuggestionMinConfidence,
    setEngagementCandidateCount,
    setEngagementTemperature,
    setSentenceElement,
    onSentenceClick,
    onTokenPointerDown,
    onTokenPointerEnter,
    onTokenPointerUp,
    selectCurrentSentenceSpan,
    handleTagClick,
    addTag,
    renameTag,
    handleTagSchemaImport,
    deleteTag,
    removeAnnotation,
    removeAnnotations,
    undoLastSpanAction,
    acceptSuggestedSpan,
    rejectSuggestedSpan,
    setActiveSuggestionTarget,
    acceptCurrentSentenceSuggestions,
    autoAnnotateDocument,
    autoAcceptDocumentSuggestions,
    rejectCurrentSentenceSuggestions,
    autoRejectDocumentSuggestions,
    reviewSuggestedSpan,
    reviewCurrentSentenceSuggestions,
    applyCurrentSentenceSuggestionReviews,
    applyDocumentSuggestionReviewsFromLlm,
    annotationForToken,
    suggestionForToken,
    isTokenInDrag: selection.isTokenInDrag,
    isTokenPending: selection.isTokenPending,
    tokenPrefix,
    tokenStyle,
    exportJsonl,
    exportProdigyJsonl,
    exportProdigyBundleZip,
    exportProdigyLabelsJson,
    exportProdigySpansJsonl,
    handleAnnotationImport,
    exportManifestJson,
    exportEventsJsonl,
    exportTagSchemaJson,
    exportRunProvenanceJson,
    exportGoldsmithBootstrapReportMd,
    exportGoldsmithReviewQueueJsonl,
    exportGoldsmithHumanChoicesJsonl,
    exportGoldsmithHardExamplesJsonl,
    exportGoldsmithBoundaryFeedbackJsonl,
    exportGoldsmithConsistencyScoresJsonl,
    exportGoldsmithCandidateRunsJsonl,
    exportGoldsmithContrastiveExamplesJsonl,
    exportGoldsmithLabelStatisticsJsonl,
    exportGoldsmithPromptPackageJsonl,
    exportGoldsmithReflectionPlansJsonl,
    exportGoldsmithRiskReasonsJsonl,
    exportGoldsmithReviewTasksJsonl,
    exportGoldsmithVerificationReportJsonl,
    verifyRebuildPreview,
    resetProjectData: readerDocuments.resetProjectData,
    refreshAuditSummary,
    refreshRunHistory,
  }
}

function assistanceAnnotationId(draftId: string, span: LocalAssistanceDraftSpan, index: number) {
  return `assistance:${draftId}:${span.tag_id}:${span.start_token_index}:${span.end_token_index}:${index}`
}
