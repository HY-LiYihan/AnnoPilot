import { computed, onMounted, ref } from 'vue'
import {
  fetchDocuments,
  fetchDocumentSummary,
  fetchSamplePresets,
  importTxt,
  loadSamplePreset as loadSamplePresetApi,
  mergeTxt,
  resetProject,
} from '../api/documents'
import {
  ACTIVE_DOCUMENT_KEY,
  PROJECT_ID,
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
  type TxtImportMode,
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

function emptyMetrics(): Metrics {
  return {
    sentence_count: 0,
    completed_count: 0,
    answer_counts: { accept: 0, reject: 0, ignore: 0, pending: 0 },
    progress: 0,
    annotation_count: 0,
    suggestion_count: 0,
    suggestion_status_counts: { pending: 0, accepted: 0, rejected: 0 },
    suggestion_source_counts: {},
    suggestion_confidence_counts: {},
    suggestion_review_counts: { accept: 0, reject: 0, uncertain: 0 },
    reviewed_suggestion_count: 0,
    accuracy: null,
    accuracy_label: 'Waiting for review data',
    calibration_count: 0,
    calibration_disagreement_count: 0,
    calibration_error_rate: null,
    review_efficiency_curves: {},
  }
}

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
  let restoreSuggestionReviewsForLoadedSentences = (_sentences: SentenceDef[]) => {}
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
    exportGoldsmithHardExamplesJsonl,
    exportGoldsmithHumanChoicesJsonl,
    exportGoldsmithReviewQueueJsonl,
    exportJsonl,
    exportManifestJson,
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

  onMounted(async () => {
    await loadDocumentList()
    await loadSamplePresets()
    const activeDocumentId = window.localStorage.getItem(ACTIVE_DOCUMENT_KEY)
    if (activeDocumentId && documents.value.some((document) => document.id === activeDocumentId)) {
      await loadDocument(activeDocumentId)
    } else if (activeDocumentId) {
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      await loadProjectTags()
      await refreshAuditSummary()
    } else if (documents.value.length) {
      await loadDocument(documents.value[0].id)
    } else {
      await loadProjectTags()
      await refreshAuditSummary()
    }
  })

  async function handleImport(file: File, mode: TxtImportMode = 'replace') {
    readerError.value = ''
    lastAnnotationImport.value = null
    isUploading.value = true
    try {
      const shouldMerge = mode === 'merge' && Boolean(documentMeta.value)
      const imported = shouldMerge && documentMeta.value
        ? await mergeTxt(PROJECT_ID, documentMeta.value.id, file)
        : await importTxt(PROJECT_ID, file)
      setTags(imported.tags, 'first')
      selection.clearSelection()
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, imported.document_id)
      await loadDocument(imported.document_id, shouldMerge)
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Import failed.'
    } finally {
      isUploading.value = false
    }
  }

  async function loadDocument(documentId: string, preserveCurrent = false) {
    try {
      const previousIndex = currentSentenceIndex.value
      const payload = await fetchDocumentSummary(PROJECT_ID, documentId)
      applyDocumentSummary(payload)
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, documentId)
      selection.clearSelection()
      currentSentenceIndex.value = preserveCurrent
        ? clampIndex(previousIndex)
        : initialSentenceIndex(payload)
      activeSuggestionId.value = ''
      if (currentSentenceIndex.value < 0) currentSentenceIndex.value = 0
      await loadSentenceWindow(documentId, currentSentenceIndex.value, true)
      await centerCurrentSentence()
      await refreshAuditSummary()
      await refreshAnnotationImportHistory(documentId)
      await refreshRunHistory()
      await refreshReviewQueue()
      await loadDocumentList()
      if (activeSession.value?.current_sentence_index !== currentSentenceIndex.value) {
        void persistSessionCursor(currentSentenceIndex.value)
      }
    } catch (error) {
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      readerError.value = error instanceof Error ? error.message : 'Could not load document.'
    }
  }

  async function loadDocumentList() {
    try {
      const payload = await fetchDocuments(PROJECT_ID)
      documents.value = payload.documents
    } catch {
      documents.value = []
    }
  }

  async function loadSamplePresets() {
    try {
      const payload = await fetchSamplePresets(PROJECT_ID)
      samplePresets.value = payload.presets
    } catch {
      samplePresets.value = []
    }
  }

  async function loadBuiltinSamplePreset(presetId: string) {
    if (!presetId || isUploading.value || isSuggesting.value) return
    isUploading.value = true
    isSuggesting.value = true
    readerError.value = ''
    lastAnnotationImport.value = null
    resetAnnotationActionState()
    try {
      const loaded = await loadSamplePresetApi(PROJECT_ID, presetId)
      setTags(loaded.tags, 'first')
      selection.clearSelection()
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, loaded.document_id)
      await loadDocument(loaded.document_id)
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not load sample preset.'
    } finally {
      isUploading.value = false
      isSuggesting.value = false
    }
  }

  async function switchDocument(documentId: string) {
    if (!documentId || documentId === documentMeta.value?.id) return
    readerError.value = ''
    lastAnnotationImport.value = null
    resetAnnotationActionState()
    selection.clearSelection()
    await loadDocument(documentId)
  }

  function applyDocumentSummary(payload: DocumentSummaryPayload) {
    documentMeta.value = payload.document
    activeSession.value = payload.session
    setTags(payload.tags)
    metrics.value = payload.metrics
    sentenceQueue.value = payload.queue
    loadedWindow.value.total = payload.metrics.sentence_count
  }

  function initialSentenceIndex(payload: DocumentSummaryPayload) {
    if (typeof payload.session.current_sentence_index === 'number') {
      return clampIndex(payload.session.current_sentence_index)
    }
    return Math.max(
      0,
      payload.queue.find((sentence) => !sentence.completed)?.index ?? 0,
    )
  }

  async function refreshDocumentSummary() {
    if (!documentMeta.value) return
    const payload = await fetchDocumentSummary(PROJECT_ID, documentMeta.value.id)
    applyDocumentSummary(payload)
    await loadDocumentList()
    await refreshReviewQueue()
  }

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
      sentence.id === sentenceId ? { ...sentence, annotations, suggestions: withoutAnnotationOverlaps(sentence.suggestions, annotations) } : sentence,
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
    return sentence.annotations.find(
      (annotation) => annotation.start_token_index <= tokenIndex && annotation.end_token_index >= tokenIndex,
    )
  }

  function tokenPrefix(sentence: SentenceDef, tokenIndex: number) {
    const token = sentence.tokens[tokenIndex]
    const previousEnd = tokenIndex === 0 ? sentence.start_char : sentence.tokens[tokenIndex - 1].end_char
    return sentence.text.slice(previousEnd - sentence.start_char, token.start_char - sentence.start_char)
  }

  function tokenStyle(sentence: SentenceDef, tokenIndex: number): Record<string, string> {
    const annotation = annotationForToken(sentence, tokenIndex)
    if (annotation) return { '--token-color': annotation.tag_color }
    const targetSuggestion = activeSuggestion.value
    if (
      targetSuggestion &&
      targetSuggestion.sentence_id === sentence.id &&
      targetSuggestion.start_token_index <= tokenIndex &&
      targetSuggestion.end_token_index >= tokenIndex
    ) {
      return { '--token-color': targetSuggestion.tag_color }
    }
    const suggestion = suggestionForToken(sentence, tokenIndex)
    if (suggestion) return { '--token-color': suggestion.tag_color }
    if ((selection.isTokenInDrag(sentence, tokenIndex) || selection.isTokenPending(sentence, tokenIndex)) && selectedTag.value) {
      return { '--token-color': selectedTag.value.color }
    }
    return {}
  }

  function suggestionForToken(sentence: SentenceDef, tokenIndex: number) {
    if (annotationForToken(sentence, tokenIndex)) return undefined
    return sentence.suggestions.find(
      (suggestion) => suggestion.start_token_index <= tokenIndex && suggestion.end_token_index >= tokenIndex,
    )
  }

  function withoutAnnotationOverlaps(suggestions: SuggestionDef[], annotations: AnnotationDef[]) {
    return suggestions.filter(
      (suggestion) =>
        !annotations.some(
          (annotation) =>
            annotation.start_token_index <= suggestion.end_token_index && annotation.end_token_index >= suggestion.start_token_index,
        ),
    )
  }

  async function resetProjectData() {
    if (isResetting.value) return
    isResetting.value = true
    readerError.value = ''
    try {
      await resetProject(PROJECT_ID)
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      documentMeta.value = null
      activeSession.value = null
      sentences.value = []
      sentenceQueue.value = []
      loadedWindow.value = { offset: 0, limit: 0, total: 0 }
      metrics.value = emptyMetrics()
      currentSentenceIndex.value = 0
      activeSuggestionId.value = ''
      resetSuggestionState()
      resetAnnotationActionState()
      sentenceElements.value = {}
      resetAuditState()
      resetReviewQueueState()
      selection.clearSelection()
      await loadDocumentList()
      await loadProjectTags()
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not reset project.'
    } finally {
      isResetting.value = false
    }
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
    handleImport,
    loadBuiltinSamplePreset,
    switchDocument,
    setCurrentSentence,
    jumpToNextReviewSentence,
    setReviewQueueOrder,
    markCurrentSentenceMonogloss,
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
    verifyRebuildPreview,
    resetProjectData,
    refreshAuditSummary,
    refreshRunHistory,
  }
}
