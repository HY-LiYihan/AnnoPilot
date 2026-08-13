import type { ComputedRef, Ref } from 'vue'
import { completeSentence, fetchDocumentSummary } from '../api/documents'
import {
  PROJECT_ID,
  type DocumentListItem,
  type DocumentMeta,
  type DocumentSummaryPayload,
  type Metrics,
  type SentenceDef,
  type SentenceQueueItem,
} from '../types/domain'

type SentenceAnswer = 'accept' | 'reject' | 'ignore'

type UseReaderSentenceCompletionOptions = {
  currentSentence: ComputedRef<SentenceDef | null>
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  documents: Ref<DocumentListItem[]>
  isSaving: Ref<boolean>
  metrics: Ref<Metrics>
  readerError: Ref<string>
  sentenceQueue: Ref<SentenceQueueItem[]>
  sentences: Ref<SentenceDef[]>
  applyDocumentSummary: (payload: DocumentSummaryPayload) => void
  loadDocumentList: () => Promise<void>
  refreshAuditSummary: () => Promise<void>
  refreshDocumentSummary: () => Promise<void>
  refreshReviewQueue: () => Promise<void>
  setCurrentSentence: (index: number, scrollBehavior?: ScrollBehavior) => void
}

export function useReaderSentenceCompletion(options: UseReaderSentenceCompletionOptions) {
  let interactiveRefreshSerial = 0

  async function completeCurrentSentence(answer: SentenceAnswer = 'accept') {
    const sentence = options.currentSentence.value
    if (!sentence || options.isSaving.value) return
    const previousCompleted = sentence.completed
    const previousAnswer = sentence.answer
    const previousIndex = options.currentSentenceIndex.value
    const nextIndex = Math.min(sentence.index + 1, Math.max(options.metrics.value.sentence_count - 1, 0))
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      applyLocalSentenceCompletion(sentence.id, true, answer)
      updateLocalCompletionMetrics(previousCompleted, previousAnswer, true, answer)
      options.setCurrentSentence(nextIndex, 'auto')
      await completeSentence(PROJECT_ID, sentence.id, true, answer)
      void refreshAfterInteractiveSave()
    } catch (error) {
      applyLocalSentenceCompletion(sentence.id, previousCompleted, previousAnswer)
      updateLocalCompletionMetrics(true, answer, previousCompleted, previousAnswer)
      options.setCurrentSentence(previousIndex, 'auto')
      options.readerError.value = error instanceof Error ? error.message : 'Could not complete sentence.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function reopenCurrentSentence() {
    const sentence = options.currentSentence.value
    if (!sentence || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      await completeSentence(PROJECT_ID, sentence.id, false, 'pending')
      sentence.completed = false
      sentence.answer = 'pending'
      options.sentenceQueue.value = options.sentenceQueue.value.map((item) =>
        item.id === sentence.id ? { ...item, completed: false, answer: 'pending' } : item,
      )
      await options.refreshDocumentSummary()
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not reopen sentence.'
    } finally {
      options.isSaving.value = false
    }
  }

  function applyLocalSentenceCompletion(sentenceId: string, completed: boolean, answer: string) {
    options.sentences.value = options.sentences.value.map((item) =>
      item.id === sentenceId ? { ...item, completed, answer } : item,
    )
    options.sentenceQueue.value = options.sentenceQueue.value.map((item) =>
      item.id === sentenceId ? { ...item, completed, answer } : item,
    )
  }

  function updateLocalCompletionMetrics(
    previousCompleted: boolean,
    previousAnswer: string,
    nextCompleted: boolean,
    nextAnswer: string,
  ) {
    const nextAnswerCounts = { ...options.metrics.value.answer_counts }
    const previousBucket = previousCompleted ? previousAnswer : 'pending'
    const nextBucket = nextCompleted ? nextAnswer : 'pending'
    nextAnswerCounts[previousBucket] = Math.max((nextAnswerCounts[previousBucket] ?? 0) - 1, 0)
    nextAnswerCounts[nextBucket] = (nextAnswerCounts[nextBucket] ?? 0) + 1
    const completedDelta = (nextCompleted ? 1 : 0) - (previousCompleted ? 1 : 0)
    const completedCount = Math.min(
      Math.max(options.metrics.value.completed_count + completedDelta, 0),
      options.metrics.value.sentence_count,
    )
    const progress = options.metrics.value.sentence_count ? completedCount / options.metrics.value.sentence_count : 0
    options.metrics.value = {
      ...options.metrics.value,
      completed_count: completedCount,
      progress,
      answer_counts: nextAnswerCounts,
    }
    options.documents.value = options.documents.value.map((document) =>
      document.id === options.documentMeta.value?.id
        ? { ...document, completed_count: completedCount, progress }
        : document,
    )
  }

  async function refreshAfterInteractiveSave() {
    const refreshSerial = ++interactiveRefreshSerial
    try {
      if (!options.documentMeta.value) return
      const payload = await fetchDocumentSummary(PROJECT_ID, options.documentMeta.value.id)
      if (refreshSerial !== interactiveRefreshSerial) return
      options.applyDocumentSummary(payload)
      await options.loadDocumentList()
      await options.refreshReviewQueue()
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not refresh workspace status.'
    }
  }

  return {
    completeCurrentSentence,
    reopenCurrentSentence,
  }
}
