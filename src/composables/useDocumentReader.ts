import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { createAnnotation, deleteAnnotation } from '../api/annotations'
import { completeSentence, documentExportUrl, fetchDocument, importTxt } from '../api/documents'
import {
  ACTIVE_DOCUMENT_KEY,
  PROJECT_ID,
  fallbackTags,
  type AnnotationDef,
  type DocumentMeta,
  type Metrics,
  type SentenceDef,
  type TagDef,
} from '../types/domain'
import { normalizedRange, useTokenSelection } from './useTokenSelection'

export function useDocumentReader() {
  const tags = ref<TagDef[]>(fallbackTags)
  const documentMeta = ref<DocumentMeta | null>(null)
  const sentences = ref<SentenceDef[]>([])
  const metrics = ref<Metrics>({
    sentence_count: 0,
    completed_count: 0,
    progress: 0,
    annotation_count: 0,
    accuracy: null,
    accuracy_label: 'Waiting for review data',
  })
  const selectedTagId = ref(fallbackTags[0].id)
  const currentSentenceIndex = ref(0)
  const isUploading = ref(false)
  const isSaving = ref(false)
  const readerError = ref('')
  const sentenceElements = ref<Record<string, HTMLElement | null>>({})

  const selection = useTokenSelection(sentences)
  const currentSentence = computed(() => sentences.value[currentSentenceIndex.value] ?? null)
  const selectedTag = computed(() => tags.value.find((tagItem) => tagItem.id === selectedTagId.value) ?? tags.value[0])
  const progressPercent = computed(() => Math.round(metrics.value.progress * 100))
  const reviewedSummary = computed(() => `${metrics.value.completed_count} / ${metrics.value.sentence_count || 0}`)
  const activeAnnotations = computed(() => currentSentence.value?.annotations ?? [])
  const queueItems = computed(() => sentences.value.slice(0, 8))

  onMounted(async () => {
    window.addEventListener('keydown', handleKeydown)
    const activeDocumentId = window.localStorage.getItem(ACTIVE_DOCUMENT_KEY)
    if (activeDocumentId) {
      await loadDocument(activeDocumentId)
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
      const payload = await fetchDocument(PROJECT_ID, documentId)
      documentMeta.value = payload.document
      sentences.value = payload.sentences
      tags.value = payload.tags.length ? payload.tags : fallbackTags
      selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0].id
      metrics.value = payload.metrics
      selection.clearSelection()
      currentSentenceIndex.value = preserveCurrent
        ? clampIndex(previousIndex)
        : Math.max(
            0,
            payload.sentences.findIndex((sentence) => !sentence.completed),
          )
      if (currentSentenceIndex.value < 0) currentSentenceIndex.value = 0
      await centerCurrentSentence()
    } catch (error) {
      window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
      readerError.value = error instanceof Error ? error.message : 'Could not load document.'
    }
  }

  function setCurrentSentence(index: number) {
    if (!sentences.value.length) return
    currentSentenceIndex.value = clampIndex(index)
    selection.clearSelection()
    void centerCurrentSentence()
  }

  async function completeCurrentSentence() {
    const sentence = currentSentence.value
    if (!sentence || isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await completeSentence(PROJECT_ID, sentence.id, true)
      sentence.completed = true
      recomputeLocalMetrics()
      setCurrentSentence(Math.min(currentSentenceIndex.value + 1, sentences.value.length - 1))
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not complete sentence.'
    } finally {
      isSaving.value = false
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null
    if (target?.matches('input, textarea, select')) return
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
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCurrentSentence(currentSentenceIndex.value + 1)
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCurrentSentence(currentSentenceIndex.value - 1)
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
      const overlaps = overlappingAnnotations(sentence, startTokenIndex, endTokenIndex)
      await Promise.all(overlaps.map((annotation) => deleteAnnotation(PROJECT_ID, annotation.id)))
      const payload = await createAnnotation(PROJECT_ID, sentence.id, tag.id, startTokenIndex, endTokenIndex)
      replaceSentenceAnnotations(sentence.id, payload.annotations)
      selection.pendingSelection.value = null
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not save annotation.'
    } finally {
      isSaving.value = false
    }
  }

  async function removeAnnotation(annotationId: string) {
    if (isSaving.value) return
    isSaving.value = true
    readerError.value = ''
    try {
      await deleteAnnotation(PROJECT_ID, annotationId)
      sentences.value = sentences.value.map((sentence) => ({
        ...sentence,
        annotations: sentence.annotations.filter((annotation) => annotation.id !== annotationId),
      }))
      recomputeLocalMetrics()
    } catch (error) {
      readerError.value = error instanceof Error ? error.message : 'Could not delete annotation.'
    } finally {
      isSaving.value = false
    }
  }

  function replaceSentenceAnnotations(sentenceId: string, annotations: AnnotationDef[]) {
    sentences.value = sentences.value.map((sentence) =>
      sentence.id === sentenceId ? { ...sentence, annotations } : sentence,
    )
    recomputeLocalMetrics()
  }

  function recomputeLocalMetrics() {
    const completedCount = sentences.value.filter((sentence) => sentence.completed).length
    const annotationCount = sentences.value.reduce((total, sentence) => total + sentence.annotations.length, 0)
    const counts = new Map<string, number>()
    for (const sentence of sentences.value) {
      for (const annotation of sentence.annotations) {
        counts.set(annotation.tag_id, (counts.get(annotation.tag_id) ?? 0) + 1)
      }
    }
    tags.value = tags.value.map((tagItem) => ({ ...tagItem, count: counts.get(tagItem.id) ?? 0 }))
    metrics.value = {
      ...metrics.value,
      sentence_count: sentences.value.length,
      completed_count: completedCount,
      progress: sentences.value.length ? completedCount / sentences.value.length : 0,
      annotation_count: annotationCount,
    }
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
    if ((selection.isTokenInDrag(sentence, tokenIndex) || selection.isTokenPending(sentence, tokenIndex)) && selectedTag.value) {
      return { '--token-color': selectedTag.value.color }
    }
    return {}
  }

  function overlappingAnnotations(sentence: SentenceDef, start: number, end: number) {
    return sentence.annotations.filter(
      (annotation) => annotation.start_token_index <= end && annotation.end_token_index >= start,
    )
  }

  function exportJsonl() {
    if (!documentMeta.value) return
    window.location.href = documentExportUrl(PROJECT_ID, documentMeta.value.id)
  }

  function clampIndex(index: number) {
    return Math.min(Math.max(index, 0), Math.max(sentences.value.length - 1, 0))
  }

  return {
    tags,
    documentMeta,
    sentences,
    metrics,
    selectedTagId,
    currentSentenceIndex,
    isUploading,
    isSaving,
    readerError,
    currentSentence,
    progressPercent,
    reviewedSummary,
    activeAnnotations,
    queueItems,
    pendingSelection: selection.pendingSelection,
    pendingSelectionText: selection.pendingSelectionText,
    handleImport,
    setCurrentSentence,
    completeCurrentSentence,
    setSentenceElement,
    onSentenceClick,
    onTokenPointerDown,
    onTokenPointerEnter,
    onTokenPointerUp,
    handleTagClick,
    removeAnnotation,
    annotationForToken,
    isTokenInDrag: selection.isTokenInDrag,
    isTokenPending: selection.isTokenPending,
    tokenPrefix,
    tokenStyle,
    exportJsonl,
  }
}
