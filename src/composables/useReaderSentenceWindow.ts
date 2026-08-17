import { computed, nextTick, type Ref } from 'vue'
import { fetchDocumentSentences, updateDocumentCursor } from '../api/documents'
import {
  PROJECT_ID,
  type DocumentListItem,
  type DocumentMeta,
  type Metrics,
  type SentenceDef,
  type SentenceQueueItem,
  type SessionState,
} from '../types/domain'

const SENTENCE_WINDOW_SIZE = 60
const SENTENCE_WINDOW_PADDING = 20

type TokenSelectionState = {
  clearSelection: () => void
}

type UseReaderSentenceWindowOptions = {
  activeSession: Ref<SessionState | null>
  activeSuggestionId: Ref<string>
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  documents: Ref<DocumentListItem[]>
  loadedWindow: Ref<{ offset: number; limit: number; total: number }>
  metrics: Ref<Metrics>
  normalizeActiveSuggestionTarget: () => void
  onSentencesLoaded: (sentences: SentenceDef[]) => void
  selection: TokenSelectionState
  sentenceElements: Ref<Record<string, HTMLElement | null>>
  sentenceQueue: Ref<SentenceQueueItem[]>
  sentences: Ref<SentenceDef[]>
}

export function useReaderSentenceWindow(options: UseReaderSentenceWindowOptions) {
  let sentenceWindowRequestSerial = 0
  const currentSentence = computed(() =>
    options.sentences.value.find((sentence) => sentence.index === options.currentSentenceIndex.value) ?? null,
  )

  async function loadSentenceWindow(documentId: string, targetIndex: number, force = false) {
    const requestSerial = ++sentenceWindowRequestSerial
    const targetLoaded = options.sentences.value.some((sentence) => sentence.index === targetIndex)
    if (!force && targetLoaded && isTargetComfortablyLoaded(targetIndex)) return

    const total = Math.max(options.metrics.value.sentence_count, options.sentenceQueue.value.length, 0)
    if (!total) {
      options.sentences.value = []
      options.loadedWindow.value = { offset: 0, limit: 0, total: 0 }
      return
    }

    const maxOffset = Math.max(total - SENTENCE_WINDOW_SIZE, 0)
    const offset = Math.min(Math.max(targetIndex - SENTENCE_WINDOW_PADDING, 0), maxOffset)
    const limit = Math.min(SENTENCE_WINDOW_SIZE, total)
    const page = await fetchDocumentSentences(PROJECT_ID, documentId, offset, limit)
    if (requestSerial !== sentenceWindowRequestSerial || options.documentMeta.value?.id !== documentId) return
    options.sentences.value = page.sentences
    options.loadedWindow.value = { offset: page.offset, limit: page.limit, total: page.total }
    options.onSentencesLoaded(page.sentences)
    options.normalizeActiveSuggestionTarget()
  }

  function setCurrentSentence(index: number, scrollBehavior: ScrollBehavior = 'smooth', targetSuggestionId = '') {
    if (!options.metrics.value.sentence_count) return
    const targetIndex = clampIndex(index)
    options.currentSentenceIndex.value = targetIndex
    options.selection.clearSelection()
    options.activeSuggestionId.value = targetSuggestionId
    void (async () => {
      const documentId = options.documentMeta.value?.id
      if (documentId) await loadSentenceWindow(documentId, targetIndex)
      if (options.currentSentenceIndex.value !== targetIndex || options.documentMeta.value?.id !== documentId) return
      if (targetSuggestionId) options.normalizeActiveSuggestionTarget()
      await centerCurrentSentence(scrollBehavior)
      void persistSessionCursor(targetIndex)
    })()
  }

  async function persistSessionCursor(index: number) {
    if (!options.documentMeta.value || options.metrics.value.sentence_count === 0) return
    try {
      const payload = await updateDocumentCursor(PROJECT_ID, options.documentMeta.value.id, index)
      options.activeSession.value = {
        id: options.activeSession.value?.id ?? 'annopilot-human',
        actor_id: options.activeSession.value?.actor_id ?? 'annopilot-human',
        current_sentence_index: payload.session.current_sentence_index,
        updated_at: payload.session.updated_at,
      }
      options.documents.value = options.documents.value.map((document) =>
        document.id === options.documentMeta.value?.id
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

  function setSentenceElement(sentenceId: string, element: unknown) {
    options.sentenceElements.value[sentenceId] = element as HTMLElement | null
  }

  async function centerCurrentSentence(behavior: ScrollBehavior = 'smooth') {
    await nextTick()
    const sentence = currentSentence.value
    if (!sentence) return
    const element = options.sentenceElements.value[sentence.id]
    const reader = element?.closest('.text-reader')
    if (!(element instanceof HTMLElement) || !(reader instanceof HTMLElement)) return

    const elementRect = element.getBoundingClientRect()
    const readerRect = reader.getBoundingClientRect()
    const centeredTop =
      reader.scrollTop + elementRect.top - readerRect.top - (reader.clientHeight - elementRect.height) / 2
    const maxScrollTop = Math.max(reader.scrollHeight - reader.clientHeight, 0)
    const top = Math.min(Math.max(centeredTop, 0), maxScrollTop)
    if (behavior === 'auto') {
      reader.scrollTop = top
      return
    }
    reader.scrollTo({ top, behavior })
  }

  function onSentenceClick(sentenceIndex: number) {
    if (sentenceIndex !== options.currentSentenceIndex.value) setCurrentSentence(sentenceIndex)
  }

  function clampIndex(index: number) {
    const total = Math.max(options.metrics.value.sentence_count, options.sentenceQueue.value.length, options.sentences.value.length)
    return Math.min(Math.max(index, 0), Math.max(total - 1, 0))
  }

  function isTargetComfortablyLoaded(targetIndex: number) {
    if (!options.sentences.value.length) return false
    const total = Math.max(options.metrics.value.sentence_count, options.sentenceQueue.value.length, options.loadedWindow.value.total)
    const firstIndex = options.sentences.value[0]?.index ?? options.loadedWindow.value.offset
    const lastIndex = options.sentences.value[options.sentences.value.length - 1]?.index ?? firstIndex
    const wholeDocumentLoaded = firstIndex === 0 && lastIndex >= total - 1
    if (wholeDocumentLoaded) return true
    const nearStart = targetIndex - firstIndex < SENTENCE_WINDOW_PADDING && firstIndex > 0
    const nearEnd = lastIndex - targetIndex < SENTENCE_WINDOW_PADDING && lastIndex < total - 1
    return !nearStart && !nearEnd
  }

  return {
    centerCurrentSentence,
    clampIndex,
    currentSentence,
    loadSentenceWindow,
    onSentenceClick,
    persistSessionCursor,
    setCurrentSentence,
    setSentenceElement,
  }
}
