import { onMounted, type Ref } from 'vue'
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
  type AnnotationImportSummary,
  type DocumentListItem,
  type DocumentMeta,
  type DocumentSummaryPayload,
  type Metrics,
  type SamplePreset,
  type SentenceDef,
  type SentenceQueueItem,
  type SessionState,
  type TagDef,
  type TxtImportMode,
} from '../types/domain'
import { initialSentenceIndex } from './readerDocumentPosition'

type SelectionState = {
  clearSelection: () => void
}

type SetTags = (tags: TagDef[], selection?: 'first' | 'preserve' | { tagId: string }) => void

type UseReaderDocumentLifecycleOptions = {
  activeSession: Ref<SessionState | null>
  activeSuggestionId: Ref<string>
  centerCurrentSentence: (behavior?: ScrollBehavior) => Promise<void>
  clampIndex: (index: number) => number
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  documents: Ref<DocumentListItem[]>
  isResetting: Ref<boolean>
  isSuggesting: Ref<boolean>
  isUploading: Ref<boolean>
  lastAnnotationImport: Ref<AnnotationImportSummary | null>
  loadedWindow: Ref<{ offset: number; limit: number; total: number }>
  loadProjectTags: () => Promise<void>
  loadSentenceWindow: (documentId: string, targetIndex: number, force?: boolean) => Promise<void>
  metrics: Ref<Metrics>
  persistSessionCursor: (index: number) => Promise<void>
  readerError: Ref<string>
  refreshAnnotationImportHistory: (documentId: string) => Promise<void>
  refreshAuditSummary: () => Promise<void>
  refreshReviewQueue: () => Promise<void>
  refreshRunHistory: () => Promise<void>
  resetAnnotationActionState: () => void
  resetAuditState: () => void
  resetReviewQueueState: () => void
  resetSuggestionState: () => void
  samplePresets: Ref<SamplePreset[]>
  selection: SelectionState
  sentenceElements: Ref<Record<string, HTMLElement | null>>
  sentenceQueue: Ref<SentenceQueueItem[]>
  sentences: Ref<SentenceDef[]>
  setTags: SetTags
}

export function emptyMetrics(): Metrics {
  return {
    sentence_count: 0,
    completed_count: 0,
    answer_counts: { accept: 0, reject: 0, ignore: 0, pending: 0 },
    progress: 0,
    annotation_count: 0,
    annotation_overlap_count: 0,
    suggestion_count: 0,
    annotation_label_counts: [],
    suggestion_label_counts: [],
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

export function useReaderDocumentLifecycle(options: UseReaderDocumentLifecycleOptions) {
  onMounted(initializeReader)

  async function initializeReader() {
    await loadDocumentList()
    await loadSamplePresets()
    const activeDocumentId = window.localStorage.getItem(ACTIVE_DOCUMENT_KEY)
    if (activeDocumentId && options.documents.value.some((document) => document.id === activeDocumentId)) {
      await loadDocument(activeDocumentId)
    } else if (activeDocumentId) {
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      await options.loadProjectTags()
      await options.refreshAuditSummary()
    } else if (options.documents.value.length) {
      await loadDocument(options.documents.value[0].id)
    } else {
      await options.loadProjectTags()
      await options.refreshAuditSummary()
    }
  }

  async function handleImport(file: File, mode: TxtImportMode = 'replace') {
    options.readerError.value = ''
    options.lastAnnotationImport.value = null
    options.isUploading.value = true
    try {
      const shouldMerge = mode === 'merge' && Boolean(options.documentMeta.value)
      const imported = shouldMerge && options.documentMeta.value
        ? await mergeTxt(PROJECT_ID, options.documentMeta.value.id, file)
        : await importTxt(PROJECT_ID, file)
      options.setTags(imported.tags, 'first')
      options.selection.clearSelection()
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, imported.document_id)
      await loadDocument(imported.document_id, shouldMerge)
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Import failed.'
    } finally {
      options.isUploading.value = false
    }
  }

  async function loadDocument(documentId: string, preserveCurrent = false) {
    try {
      const previousIndex = options.currentSentenceIndex.value
      const payload = await fetchDocumentSummary(PROJECT_ID, documentId)
      applyDocumentSummary(payload)
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, documentId)
      options.selection.clearSelection()
      options.currentSentenceIndex.value = preserveCurrent
        ? options.clampIndex(previousIndex)
        : initialSentenceIndex(payload, options.clampIndex)
      options.activeSuggestionId.value = ''
      if (options.currentSentenceIndex.value < 0) options.currentSentenceIndex.value = 0
      await options.loadSentenceWindow(documentId, options.currentSentenceIndex.value, true)
      await options.centerCurrentSentence()
      await options.refreshAuditSummary()
      await options.refreshAnnotationImportHistory(documentId)
      await options.refreshRunHistory()
      await options.refreshReviewQueue()
      await loadDocumentList()
      if (options.activeSession.value?.current_sentence_index !== options.currentSentenceIndex.value) {
        void options.persistSessionCursor(options.currentSentenceIndex.value)
      }
    } catch (error) {
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      options.readerError.value = error instanceof Error ? error.message : 'Could not load document.'
    }
  }

  async function loadDocumentList() {
    try {
      const payload = await fetchDocuments(PROJECT_ID)
      options.documents.value = payload.documents
    } catch {
      options.documents.value = []
    }
  }

  async function loadSamplePresets() {
    try {
      const payload = await fetchSamplePresets(PROJECT_ID)
      options.samplePresets.value = payload.presets
    } catch {
      options.samplePresets.value = []
    }
  }

  async function loadBuiltinSamplePreset(presetId: string) {
    if (!presetId || options.isUploading.value || options.isSuggesting.value) return
    options.isUploading.value = true
    options.isSuggesting.value = true
    options.readerError.value = ''
    options.lastAnnotationImport.value = null
    options.resetAnnotationActionState()
    try {
      const preset = options.samplePresets.value.find((item) => item.id === presetId)
      const loaded = await loadSamplePresetApi(PROJECT_ID, presetId, {
        autoAcceptSuggestions: preset?.auto_accept_on_load ?? false,
        completeSentences: preset?.complete_sentences_on_load ?? false,
      })
      options.setTags(loaded.tags, 'first')
      options.selection.clearSelection()
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, loaded.document_id)
      await loadDocument(loaded.document_id)
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not load sample preset.'
    } finally {
      options.isUploading.value = false
      options.isSuggesting.value = false
    }
  }

  async function switchDocument(documentId: string) {
    if (!documentId || documentId === options.documentMeta.value?.id) return
    options.readerError.value = ''
    options.lastAnnotationImport.value = null
    options.resetAnnotationActionState()
    options.selection.clearSelection()
    await loadDocument(documentId)
  }

  function applyDocumentSummary(payload: DocumentSummaryPayload) {
    options.documentMeta.value = payload.document
    options.activeSession.value = payload.session
    options.setTags(payload.tags)
    options.metrics.value = payload.metrics
    options.sentenceQueue.value = payload.queue
    options.loadedWindow.value.total = payload.metrics.sentence_count
  }

  async function refreshDocumentSummary() {
    if (!options.documentMeta.value) return
    const payload = await fetchDocumentSummary(PROJECT_ID, options.documentMeta.value.id)
    applyDocumentSummary(payload)
    await loadDocumentList()
    await options.refreshReviewQueue()
  }

  async function resetProjectData() {
    if (options.isResetting.value) return
    options.isResetting.value = true
    options.readerError.value = ''
    try {
      await resetProject(PROJECT_ID)
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      options.documentMeta.value = null
      options.activeSession.value = null
      options.sentences.value = []
      options.sentenceQueue.value = []
      options.loadedWindow.value = { offset: 0, limit: 0, total: 0 }
      options.metrics.value = emptyMetrics()
      options.currentSentenceIndex.value = 0
      options.activeSuggestionId.value = ''
      options.resetSuggestionState()
      options.resetAnnotationActionState()
      options.sentenceElements.value = {}
      options.resetAuditState()
      options.resetReviewQueueState()
      options.selection.clearSelection()
      await loadDocumentList()
      await options.loadProjectTags()
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not reset project.'
    } finally {
      options.isResetting.value = false
    }
  }

  return {
    applyDocumentSummary,
    handleImport,
    initializeReader,
    loadBuiltinSamplePreset,
    loadDocument,
    loadDocumentList,
    loadSamplePresets,
    refreshDocumentSummary,
    resetProjectData,
    switchDocument,
  }
}
