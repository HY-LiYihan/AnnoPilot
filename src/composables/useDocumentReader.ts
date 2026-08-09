import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { createAnnotation, deleteAnnotation } from '../api/annotations'
import { fetchAuditSummary, previewRebuild } from '../api/audit'
import {
  completeSentence,
  documentExportUrl,
  eventsExportUrl,
  fetchDocuments,
  fetchDocumentSentences,
  fetchDocumentSummary,
  fetchReviewQueue,
  importAnnotationsJsonl,
  importTxt,
  manifestExportUrl,
  prodigyExportUrl,
  tagSchemaExportUrl,
  updateDocumentCursor,
} from '../api/documents'
import { fetchRuns, runProvenanceExportUrl } from '../api/runs'
import {
  acceptSuggestion,
  acceptSentenceSuggestions,
  autoAcceptSuggestions,
  autoRejectSuggestions,
  generateSentenceSuggestions,
  generateSuggestions,
  rejectSuggestion,
  rejectSentenceSuggestions,
  reviewSuggestion,
} from '../api/suggestions'
import { createTag, deleteTag as deleteProjectTag, fetchTags, importTagSchema, renameTag as renameProjectTag } from '../api/tags'
import {
  ACTIVE_DOCUMENT_KEY,
  PROJECT_ID,
  fallbackTags,
  type AuditSummary,
  type AnnotationRun,
  type AnnotationDef,
  type DocumentMeta,
  type DocumentListItem,
  type DocumentSummaryPayload,
  type Metrics,
  type RebuildPreview,
  type ReviewQueueItem,
  type SentenceDef,
  type SentenceQueueItem,
  type SessionState,
  type SuggestionDef,
  type SuggestionReview,
  type TagDef,
} from '../types/domain'
import { normalizedRange, useTokenSelection } from './useTokenSelection'

const SENTENCE_WINDOW_SIZE = 60
const SENTENCE_WINDOW_PADDING = 20

type UndoableSpanAction =
  | {
      kind: 'created'
      label: string
      sentenceId: string
      createdAnnotationIds: string[]
      restoredAnnotations: AnnotationDef[]
    }
  | {
      kind: 'deleted'
      label: string
      sentenceId: string
      annotation: AnnotationDef
    }

export function useDocumentReader() {
  const tags = ref<TagDef[]>(fallbackTags)
  const documents = ref<DocumentListItem[]>([])
  const documentMeta = ref<DocumentMeta | null>(null)
  const activeSession = ref<SessionState | null>(null)
  const sentences = ref<SentenceDef[]>([])
  const sentenceQueue = ref<SentenceQueueItem[]>([])
  const loadedWindow = ref({ offset: 0, limit: 0, total: 0 })
  const metrics = ref<Metrics>({
    sentence_count: 0,
    completed_count: 0,
    answer_counts: { accept: 0, reject: 0, ignore: 0, pending: 0 },
    progress: 0,
    annotation_count: 0,
    suggestion_count: 0,
    accuracy: null,
    accuracy_label: 'Waiting for review data',
  })
  const selectedTagId = ref(fallbackTags[0].id)
  const currentSentenceIndex = ref(0)
  const suggestionLimit = ref(6)
  const suggestionMinConfidence = ref(0.7)
  const isUploading = ref(false)
  const isSaving = ref(false)
  const isSuggesting = ref(false)
  const isVerifyingRebuild = ref(false)
  const readerError = ref('')
  const auditSummary = ref<AuditSummary | null>(null)
  const rebuildPreview = ref<RebuildPreview | null>(null)
  const runHistory = ref<AnnotationRun[]>([])
  const reviewQueueDetails = ref<ReviewQueueItem[]>([])
  const reviewQueueTotal = ref(0)
  const suggestionReviews = ref<Record<string, SuggestionReview>>({})
  const reviewingSuggestionId = ref('')
  const lastUndoAction = ref<UndoableSpanAction | null>(null)
  const sentenceElements = ref<Record<string, HTMLElement | null>>({})

  const selection = useTokenSelection(sentences)
  const currentSentence = computed(() => sentences.value.find((sentence) => sentence.index === currentSentenceIndex.value) ?? null)
  const selectedTag = computed(() => tags.value.find((tagItem) => tagItem.id === selectedTagId.value) ?? tags.value[0] ?? null)
  const progressPercent = computed(() => Math.round(metrics.value.progress * 100))
  const reviewedSummary = computed(() => `${metrics.value.completed_count} / ${metrics.value.sentence_count || 0}`)
  const reviewSummary = computed(() => `${metrics.value.suggestion_count} 待确认`)
  const activeAnnotations = computed(() => currentSentence.value?.annotations ?? [])
  const activeSuggestions = computed(() => currentSentence.value?.suggestions ?? [])
  const reviewQueueItems = computed(() => sentenceQueue.value.filter((sentence) => !sentence.completed && sentence.suggestion_count > 0))
  const reviewQueueSummary = computed(() => {
    if (!reviewQueueItems.value.length) return 'No review queue'
    const queueIndex = reviewQueueItems.value.findIndex((sentence) => sentence.index === currentSentenceIndex.value)
    return queueIndex >= 0 ? `Review ${queueIndex + 1}/${reviewQueueItems.value.length}` : `${reviewQueueItems.value.length} pending reviews`
  })
  const canUndoSpanAction = computed(() => Boolean(lastUndoAction.value))
  const undoLabel = computed(() => lastUndoAction.value?.label ?? 'Undo span')
  const hasReviewQueue = computed(() => sentenceQueue.value.some((sentence) => !sentence.completed && sentence.suggestion_count > 0))
  const queueItems = computed(() => sentenceQueue.value)

  onMounted(async () => {
    window.addEventListener('keydown', handleKeydown)
    await loadDocumentList()
    const activeDocumentId = window.localStorage.getItem(ACTIVE_DOCUMENT_KEY)
    if (activeDocumentId) {
      await loadDocument(activeDocumentId)
    } else if (documents.value.length) {
      await loadDocument(documents.value[0].id)
    } else {
      await loadProjectTags()
      await refreshAuditSummary()
    }
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleKeydown)
  })

  async function handleImport(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    if (!file) return

    readerError.value = ''
    isUploading.value = true
    try {
      const imported = await importTxt(PROJECT_ID, file)
      tags.value = imported.tags
      selectedTagId.value = imported.tags[0]?.id ?? selectedTagId.value
      selection.clearSelection()
      window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, imported.document_id)
      await loadDocument(imported.document_id)
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Import failed.'
    } finally {
      isUploading.value = false
      input.value = ''
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
      if (currentSentenceIndex.value < 0) currentSentenceIndex.value = 0
      await loadSentenceWindow(documentId, currentSentenceIndex.value, true)
      await centerCurrentSentence()
      await refreshAuditSummary()
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

  async function switchDocument(documentId: string) {
    if (!documentId || documentId === documentMeta.value?.id) return
    readerError.value = ''
    lastUndoAction.value = null
    selection.clearSelection()
    await loadDocument(documentId)
  }

  function applyDocumentSummary(payload: DocumentSummaryPayload) {
    documentMeta.value = payload.document
    activeSession.value = payload.session
    tags.value = payload.tags.length ? payload.tags : fallbackTags
    selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0]?.id ?? ''
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

  async function loadProjectTags() {
    try {
      const payload = await fetchTags(PROJECT_ID)
      tags.value = payload.tags.length ? payload.tags : fallbackTags
      selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0]?.id ?? ''
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not load project tags.'
    }
  }

  async function refreshDocumentSummary() {
    if (!documentMeta.value) return
    const payload = await fetchDocumentSummary(PROJECT_ID, documentMeta.value.id)
    applyDocumentSummary(payload)
    await loadDocumentList()
    await refreshReviewQueue()
  }

  async function refreshReviewQueue() {
    if (!documentMeta.value) {
      reviewQueueDetails.value = []
      reviewQueueTotal.value = 0
      return
    }
    try {
      const payload = await fetchReviewQueue(PROJECT_ID, documentMeta.value.id)
      reviewQueueDetails.value = payload.items
      reviewQueueTotal.value = payload.total
    } catch {
      reviewQueueDetails.value = []
      reviewQueueTotal.value = 0
    }
  }

  async function loadSentenceWindow(documentId: string, targetIndex: number, force = false) {
    const targetLoaded = sentences.value.some((sentence) => sentence.index === targetIndex)
    if (!force && targetLoaded && isTargetComfortablyLoaded(targetIndex)) return

    const total = Math.max(metrics.value.sentence_count, sentenceQueue.value.length, 0)
    if (!total) {
      sentences.value = []
      loadedWindow.value = { offset: 0, limit: 0, total: 0 }
      return
    }

    const maxOffset = Math.max(total - SENTENCE_WINDOW_SIZE, 0)
    const offset = Math.min(Math.max(targetIndex - SENTENCE_WINDOW_PADDING, 0), maxOffset)
    const limit = Math.min(SENTENCE_WINDOW_SIZE, total)
    const page = await fetchDocumentSentences(PROJECT_ID, documentId, offset, limit)
    sentences.value = page.sentences
    loadedWindow.value = { offset: page.offset, limit: page.limit, total: page.total }
    restoreSuggestionReviews(page.sentences)
  }

  function isTargetComfortablyLoaded(targetIndex: number) {
    if (!sentences.value.length) return false
    const total = Math.max(metrics.value.sentence_count, sentenceQueue.value.length, loadedWindow.value.total)
    const firstIndex = sentences.value[0]?.index ?? loadedWindow.value.offset
    const lastIndex = sentences.value[sentences.value.length - 1]?.index ?? firstIndex
    const wholeDocumentLoaded = firstIndex === 0 && lastIndex >= total - 1
    if (wholeDocumentLoaded) return true
    const nearStart = targetIndex - firstIndex < SENTENCE_WINDOW_PADDING && firstIndex > 0
    const nearEnd = lastIndex - targetIndex < SENTENCE_WINDOW_PADDING && lastIndex < total - 1
    return !nearStart && !nearEnd
  }

  function setCurrentSentence(index: number) {
    if (!metrics.value.sentence_count) return
    currentSentenceIndex.value = clampIndex(index)
    selection.clearSelection()
    void (async () => {
      if (documentMeta.value) await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value)
      await centerCurrentSentence()
      void persistSessionCursor(currentSentenceIndex.value)
    })()
  }

  async function persistSessionCursor(index: number) {
    if (!documentMeta.value || metrics.value.sentence_count === 0) return
    try {
      const payload = await updateDocumentCursor(PROJECT_ID, documentMeta.value.id, index)
      activeSession.value = {
        id: activeSession.value?.id ?? 'annopilot-human',
        actor_id: activeSession.value?.actor_id ?? 'annopilot-human',
        current_sentence_index: payload.session.current_sentence_index,
        updated_at: payload.session.updated_at,
      }
      documents.value = documents.value.map((document) =>
        document.id === documentMeta.value?.id
          ? {
              ...document,
              current_sentence_index: payload.session.current_sentence_index,
              session_updated_at: payload.session.updated_at,
            }
          : document,
      )
    } catch {
      // Cursor persistence is best-effort runtime state; annotation mutations still surface errors elsewhere.
    }
  }

  function jumpToNextReviewSentence() {
    if (!reviewQueueItems.value.length) return
    const startIndex = currentSentenceIndex.value + 1
    const next = reviewQueueItems.value.find((sentence) => sentence.index >= startIndex)
    const wrapped = reviewQueueItems.value[0]
    const target = next ?? wrapped
    if (target) setCurrentSentence(target.index)
  }

  function jumpToNextReviewIfCurrentCleared() {
    if (activeSuggestions.value.length === 0 && hasReviewQueue.value) jumpToNextReviewSentence()
  }

  async function completeCurrentSentence(answer: 'accept' | 'reject' | 'ignore' = 'accept') {
    const sentence = currentSentence.value
    if (!sentence || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await completeSentence(PROJECT_ID, sentence.id, true, answer)
      sentence.completed = true
      sentence.answer = answer
      sentenceQueue.value = sentenceQueue.value.map((item) => (item.id === sentence.id ? { ...item, completed: true, answer } : item))
      await refreshDocumentSummary()
      await refreshAuditSummary()
      setCurrentSentence(Math.min(currentSentenceIndex.value + 1, Math.max(metrics.value.sentence_count - 1, 0)))
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not complete sentence.'
    } finally {
      isSaving.value = false
    }
  }

  async function reopenCurrentSentence() {
    const sentence = currentSentence.value
    if (!sentence || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await completeSentence(PROJECT_ID, sentence.id, false, 'pending')
      sentence.completed = false
      sentence.answer = 'pending'
      sentenceQueue.value = sentenceQueue.value.map((item) =>
        item.id === sentence.id ? { ...item, completed: false, answer: 'pending' } : item,
      )
      await refreshDocumentSummary()
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not reopen sentence.'
    } finally {
      isSaving.value = false
    }
  }

  async function generateDocumentSuggestions() {
    if (!documentMeta.value || isSuggesting.value) return
    isSuggesting.value = true
    readerError.value = ''
    try {
      await generateSuggestions(PROJECT_ID, documentMeta.value.id, suggestionLimit.value, suggestionMinConfidence.value)
      await loadDocument(documentMeta.value.id, true)
      await refreshRunHistory()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not generate suggestions.'
    } finally {
      isSuggesting.value = false
    }
  }

  async function generateCurrentSentenceSuggestions() {
    const sentence = currentSentence.value
    if (!documentMeta.value || !sentence || isSuggesting.value) return
    isSuggesting.value = true
    readerError.value = ''
    try {
      await generateSentenceSuggestions(PROJECT_ID, documentMeta.value.id, sentence.id, suggestionLimit.value, suggestionMinConfidence.value)
      await refreshDocumentSummary()
      await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      await refreshRunHistory()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not generate current sentence suggestions.'
    } finally {
      isSuggesting.value = false
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

  function handleKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null
    if (target?.matches('input, textarea, select') || target?.isContentEditable) return
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault()
      void undoLastSpanAction()
      return
    }
    const shortcutTag = tags.value.find((tagItem) => tagItem.shortcut === event.key)
    if (shortcutTag) {
      event.preventDefault()
      void applyTagToSelection(shortcutTag.id)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      void completeCurrentSentence()
    }
    if (event.key.toLowerCase() === 'i') {
      event.preventDefault()
      void completeCurrentSentence('ignore')
    }
    if (event.key.toLowerCase() === 'j') {
      event.preventDefault()
      void completeCurrentSentence('reject')
    }
    if (event.key.toLowerCase() === 'e') {
      event.preventDefault()
      void reopenCurrentSentence()
    }
    if ((event.code === 'Space' || event.key === ' ') && !target?.matches('button, a')) {
      event.preventDefault()
      void completeCurrentSentence('ignore')
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCurrentSentence(currentSentenceIndex.value + 1)
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCurrentSentence(currentSentenceIndex.value - 1)
    }
    if (event.key.toLowerCase() === 'a') {
      event.preventDefault()
      void acceptCurrentSentenceSuggestions()
    }
    if (event.key.toLowerCase() === 'x') {
      event.preventDefault()
      void rejectCurrentSentenceSuggestions()
    }
    if (event.key.toLowerCase() === 'y') {
      event.preventDefault()
      const suggestion = activeSuggestions.value[0]
      if (suggestion) void acceptSuggestedSpan(suggestion)
    }
    if (event.key.toLowerCase() === 'n') {
      event.preventDefault()
      const suggestion = activeSuggestions.value[0]
      if (suggestion) void rejectSuggestedSpan(suggestion)
    }
    if (event.key.toLowerCase() === 'r') {
      event.preventDefault()
      jumpToNextReviewSentence()
    }
  }

  function setSentenceElement(sentenceId: string, element: unknown) {
    sentenceElements.value[sentenceId] = element as HTMLElement | null
  }

  async function centerCurrentSentence() {
    await nextTick()
    const sentence = currentSentence.value
    if (!sentence) return
    sentenceElements.value[sentence.id]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  function onSentenceClick(sentenceIndex: number) {
    if (sentenceIndex !== currentSentenceIndex.value) setCurrentSentence(sentenceIndex)
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

  function handleTagClick(tagId: string) {
    void applyTagToSelection(tagId)
  }

  async function addTag(name: string) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const payload = await createTag(PROJECT_ID, name)
      tags.value = [...tags.value, payload.tag]
      selectedTagId.value = payload.tag.id
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not create tag.'
    } finally {
      isSaving.value = false
    }
  }

  async function renameTag(tag: TagDef, name: string, description?: string | null, examples?: string[]) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const payload = await renameProjectTag(PROJECT_ID, tag.id, name, description, examples)
      tags.value = tags.value.map((tagItem) => (tagItem.id === tag.id ? payload.tag : tagItem))
      if (documentMeta.value) {
        await refreshDocumentSummary()
        await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      }
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not rename tag.'
    } finally {
      isSaving.value = false
    }
  }

  async function handleTagSchemaImport(file: File) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const schema = JSON.parse(await file.text())
      const payload = await importTagSchema(PROJECT_ID, schema)
      tags.value = payload.tags.length ? payload.tags : fallbackTags
      selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0]?.id ?? ''
      if (documentMeta.value) {
        await refreshDocumentSummary()
        await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      }
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not import tag schema.'
    } finally {
      isSaving.value = false
    }
  }

  async function deleteTag(tag: TagDef) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await deleteProjectTag(PROJECT_ID, tag.id)
      if (documentMeta.value) {
        await loadDocument(documentMeta.value.id, true)
      } else {
        tags.value = tags.value.filter((tagItem) => tagItem.id !== tag.id)
        selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0]?.id ?? ''
      }
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not delete tag.'
    } finally {
      isSaving.value = false
    }
  }

  async function applyTagToSelection(tagId: string) {
    selectedTagId.value = tagId
    const pendingSelection = selection.pendingSelection.value
    if (!pendingSelection) return
    const sentence = sentences.value.find((item) => item.id === pendingSelection.sentenceId)
    if (!sentence) return
    await createSentenceAnnotation(sentence, pendingSelection.start, pendingSelection.end, tagId)
  }

  async function createSentenceAnnotation(sentence: SentenceDef, start: number, end: number, tagId: string) {
    const tag = tags.value.find((tagItem) => tagItem.id === tagId)
    if (!tag || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    const [startTokenIndex, endTokenIndex] = normalizedRange(start, end)
    try {
      const previousAnnotationIds = new Set(sentence.annotations.map((annotation) => annotation.id))
      const overlaps = overlappingAnnotations(sentence, startTokenIndex, endTokenIndex)
      await Promise.all(overlaps.map((annotation) => deleteAnnotation(PROJECT_ID, annotation.id)))
      const payload = await createAnnotation(PROJECT_ID, sentence.id, tag.id, startTokenIndex, endTokenIndex)
      const createdAnnotationIds = payload.annotations
        .filter((annotation) => !previousAnnotationIds.has(annotation.id))
        .map((annotation) => annotation.id)
      replaceSentenceAnnotations(sentence.id, payload.annotations)
      selection.pendingSelection.value = null
      lastUndoAction.value = {
        kind: 'created',
        label: `Undo ${tag.name}`,
        sentenceId: sentence.id,
        createdAnnotationIds,
        restoredAnnotations: overlaps.filter((annotation) => annotation.source === 'human'),
      }
      await refreshDocumentSummary()
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not save annotation.'
    } finally {
      isSaving.value = false
    }
  }

  async function removeAnnotation(annotationId: string) {
    if (isSaving.value) return
    const removedFromSentence = sentences.value.find((sentence) => sentence.annotations.some((annotation) => annotation.id === annotationId))
    const removedAnnotation = removedFromSentence?.annotations.find((annotation) => annotation.id === annotationId)
    isSaving.value = true
    readerError.value = ''
    try {
      await deleteAnnotation(PROJECT_ID, annotationId)
      sentences.value = sentences.value.map((sentence) => ({
        ...sentence,
        annotations: sentence.annotations.filter((annotation) => annotation.id !== annotationId),
      }))
      lastUndoAction.value = removedFromSentence && removedAnnotation?.source === 'human'
        ? {
            kind: 'deleted',
            label: `Restore ${removedAnnotation.tag_name}`,
            sentenceId: removedFromSentence.id,
            annotation: removedAnnotation,
          }
        : null
      await refreshDocumentSummary()
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not delete annotation.'
    } finally {
      isSaving.value = false
    }
  }

  async function undoLastSpanAction() {
    const action = lastUndoAction.value
    if (!action || !documentMeta.value || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      if (action.kind === 'created') {
        await Promise.all(action.createdAnnotationIds.map((annotationId) => deleteAnnotation(PROJECT_ID, annotationId)))
        for (const annotation of action.restoredAnnotations) {
          await createAnnotation(
            PROJECT_ID,
            action.sentenceId,
            annotation.tag_id,
            annotation.start_token_index,
            annotation.end_token_index,
          )
        }
      } else {
        const loadedSentence = sentences.value.find((sentence) => sentence.id === action.sentenceId)
        const overlaps = loadedSentence
          ? overlappingAnnotations(loadedSentence, action.annotation.start_token_index, action.annotation.end_token_index)
          : []
        if (overlaps.length) {
          readerError.value = 'Cannot undo because that span now overlaps another annotation.'
          return
        }
        await createAnnotation(
          PROJECT_ID,
          action.sentenceId,
          action.annotation.tag_id,
          action.annotation.start_token_index,
          action.annotation.end_token_index,
        )
      }
      lastUndoAction.value = null
      await refreshDocumentSummary()
      await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not undo the last span action.'
    } finally {
      isSaving.value = false
    }
  }

  async function acceptSuggestedSpan(suggestion: SuggestionDef) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const payload = await acceptSuggestion(PROJECT_ID, suggestion.id)
      replaceSentenceAnnotations(suggestionSentenceId(suggestion), payload.annotations)
      removeSuggestion(suggestion.id)
      await refreshDocumentSummary()
      await refreshAuditSummary()
      jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not accept suggestion.'
    } finally {
      isSaving.value = false
    }
  }

  async function acceptCurrentSentenceSuggestions() {
    const sentence = currentSentence.value
    if (!sentence || !activeSuggestions.value.length || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const payload = await acceptSentenceSuggestions(PROJECT_ID, sentence.id)
      replaceSentenceAnnotations(sentence.id, payload.annotations)
      await refreshDocumentSummary()
      if (documentMeta.value) await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      await refreshAuditSummary()
      jumpToNextReviewIfCurrentCleared()
      if (payload.accepted === 0 && payload.skipped > 0) {
        readerError.value = 'Current suggestions overlap existing annotations and were skipped.'
      }
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not accept current suggestions.'
    } finally {
      isSaving.value = false
    }
  }

  async function autoAcceptDocumentSuggestions() {
    if (!documentMeta.value || metrics.value.suggestion_count === 0 || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const result = await autoAcceptSuggestions(PROJECT_ID, documentMeta.value.id, suggestionMinConfidence.value)
      await loadDocument(documentMeta.value.id, true)
      if (result.accepted === 0) {
        readerError.value = `No suggestions met the ${Math.round(result.min_confidence * 100)}% threshold.`
      }
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not auto-accept suggestions.'
    } finally {
      isSaving.value = false
    }
  }

  async function rejectSuggestedSpan(suggestion: SuggestionDef) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await rejectSuggestion(PROJECT_ID, suggestion.id)
      removeSuggestion(suggestion.id)
      await refreshDocumentSummary()
      await refreshAuditSummary()
      jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not reject suggestion.'
    } finally {
      isSaving.value = false
    }
  }

  async function rejectCurrentSentenceSuggestions() {
    const sentence = currentSentence.value
    if (!sentence || !activeSuggestions.value.length || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await rejectSentenceSuggestions(PROJECT_ID, sentence.id)
      await refreshDocumentSummary()
      if (documentMeta.value) await loadSentenceWindow(documentMeta.value.id, currentSentenceIndex.value, true)
      await refreshAuditSummary()
      jumpToNextReviewIfCurrentCleared()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not reject current suggestions.'
    } finally {
      isSaving.value = false
    }
  }

  async function autoRejectDocumentSuggestions() {
    if (!documentMeta.value || metrics.value.suggestion_count === 0 || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      const result = await autoRejectSuggestions(PROJECT_ID, documentMeta.value.id)
      await loadDocument(documentMeta.value.id, true)
      if (result.rejected === 0) {
        readerError.value = 'No pending suggestions to reject.'
      }
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not auto-reject suggestions.'
    } finally {
      isSaving.value = false
    }
  }

  async function reviewSuggestedSpan(suggestion: SuggestionDef) {
    if (reviewingSuggestionId.value) return
    reviewingSuggestionId.value = suggestion.id
    readerError.value = ''
    try {
      const review = await reviewSuggestion(PROJECT_ID, suggestion.id)
      suggestionReviews.value = { ...suggestionReviews.value, [suggestion.id]: review }
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not review suggestion with LLM.'
    } finally {
      reviewingSuggestionId.value = ''
    }
  }

  function replaceSentenceAnnotations(sentenceId: string, annotations: AnnotationDef[]) {
    sentences.value = sentences.value.map((sentence) =>
      sentence.id === sentenceId ? { ...sentence, annotations, suggestions: withoutAnnotationOverlaps(sentence.suggestions, annotations) } : sentence,
    )
  }

  function removeSuggestion(suggestionId: string) {
    sentences.value = sentences.value.map((sentence) => ({
      ...sentence,
      suggestions: sentence.suggestions.filter((suggestion) => suggestion.id !== suggestionId),
    }))
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

  function overlappingAnnotations(sentence: SentenceDef, start: number, end: number) {
    return sentence.annotations.filter(
      (annotation) => annotation.start_token_index <= end && annotation.end_token_index >= start,
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

  function suggestionSentenceId(suggestion: SuggestionDef) {
    return suggestion.sentence_id
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

  function exportJsonl() {
    if (!documentMeta.value) return
    window.location.href = documentExportUrl(PROJECT_ID, documentMeta.value.id)
  }

  function exportProdigyJsonl() {
    if (!documentMeta.value) return
    window.location.href = prodigyExportUrl(PROJECT_ID, documentMeta.value.id)
  }

  async function handleAnnotationImport(file: File) {
    if (!documentMeta.value || isUploading.value) return
    isUploading.value = true
    readerError.value = ''
    try {
      await importAnnotationsJsonl(PROJECT_ID, documentMeta.value.id, file)
      await loadDocument(documentMeta.value.id, true)
      await refreshAuditSummary()
      await refreshRunHistory()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not import annotation JSONL.'
    } finally {
      isUploading.value = false
    }
  }

  function exportManifestJson() {
    if (!documentMeta.value) return
    window.location.href = manifestExportUrl(PROJECT_ID, documentMeta.value.id)
  }

  function exportEventsJsonl() {
    window.location.href = eventsExportUrl(PROJECT_ID)
    void refreshAuditSummary()
  }

  function exportTagSchemaJson() {
    window.location.href = tagSchemaExportUrl(PROJECT_ID)
  }

  function exportRunProvenanceJson(runId: string) {
    window.location.href = runProvenanceExportUrl(PROJECT_ID, runId)
  }

  async function refreshAuditSummary() {
    try {
      auditSummary.value = await fetchAuditSummary(PROJECT_ID)
    } catch {
      auditSummary.value = null
    }
  }

  async function verifyRebuildPreview() {
    if (isVerifyingRebuild.value) return
    isVerifyingRebuild.value = true
    readerError.value = ''
    try {
      rebuildPreview.value = await previewRebuild(PROJECT_ID)
      await refreshAuditSummary()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not verify rebuild.'
    } finally {
      isVerifyingRebuild.value = false
    }
  }

  async function refreshRunHistory() {
    try {
      const payload = await fetchRuns(PROJECT_ID, documentMeta.value?.id, 5)
      runHistory.value = payload.runs
    } catch {
      runHistory.value = []
    }
  }

  function clampIndex(index: number) {
    const total = Math.max(metrics.value.sentence_count, sentenceQueue.value.length, sentences.value.length)
    return Math.min(Math.max(index, 0), Math.max(total - 1, 0))
  }

  return {
    tags,
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
    isVerifyingRebuild,
    readerError,
    auditSummary,
    rebuildPreview,
    runHistory,
    reviewQueueDetails,
    reviewQueueTotal,
    suggestionReviews,
    reviewingSuggestionId,
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
    switchDocument,
    setCurrentSentence,
    jumpToNextReviewSentence,
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
    handleTagClick,
    addTag,
    renameTag,
    handleTagSchemaImport,
    deleteTag,
    removeAnnotation,
    undoLastSpanAction,
    acceptSuggestedSpan,
    rejectSuggestedSpan,
    acceptCurrentSentenceSuggestions,
    autoAcceptDocumentSuggestions,
    rejectCurrentSentenceSuggestions,
    autoRejectDocumentSuggestions,
    reviewSuggestedSpan,
    annotationForToken,
    suggestionForToken,
    isTokenInDrag: selection.isTokenInDrag,
    isTokenPending: selection.isTokenPending,
    tokenPrefix,
    tokenStyle,
    exportJsonl,
    exportProdigyJsonl,
    handleAnnotationImport,
    exportManifestJson,
    exportEventsJsonl,
    exportTagSchemaJson,
    exportRunProvenanceJson,
    verifyRebuildPreview,
    refreshAuditSummary,
    refreshRunHistory,
  }
}
