import { computed, ref } from 'vue'
import {
  type AnnotationDef,
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
import { emptyMetrics, useReaderDocumentLifecycle } from './useReaderDocumentLifecycle'
import {
  annotationForToken as findAnnotationForToken,
  suggestionForToken as findSuggestionForToken,
  suggestionsWithoutAnnotationOverlaps,
  tokenPrefix as getTokenPrefix,
  tokenStyleForToken,
} from './readerTokenDisplay'

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
    completeCurrentSentence,
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

  const {
    applyTagToSelection,
    autoMarkEmptySentencesMonogloss,
    canUndoSpanAction,
    handleTagClick,
    markCurrentSentenceMonogloss,
    removeAnnotation,
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

  const {
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
    return findAnnotationForToken(sentence, tokenIndex)
  }

  function tokenPrefix(sentence: SentenceDef, tokenIndex: number) {
    return getTokenPrefix(sentence, tokenIndex)
  }

  function tokenStyle(sentence: SentenceDef, tokenIndex: number): Record<string, string> {
    return tokenStyleForToken(sentence, tokenIndex, {
      activeSuggestion: activeSuggestion.value,
      selectedTag: selectedTag.value,
      isTokenInDrag: selection.isTokenInDrag,
      isTokenPending: selection.isTokenPending,
    })
  }

  function suggestionForToken(sentence: SentenceDef, tokenIndex: number) {
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
    activeSuggestions,
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
    reopenCurrentSentence,
    generateDocumentSuggestions,
    generateCurrentSentenceSuggestions,
    setSuggestionLimit,
    setSuggestionMinConfidence,
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
    verifyRebuildPreview,
    resetProjectData: readerDocuments.resetProjectData,
    refreshAuditSummary,
    refreshRunHistory,
  }
}
