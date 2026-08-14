import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { fetchReviewQueue } from '../api/documents'
import {
  PROJECT_ID,
  type DocumentMeta,
  type ReviewQueueItem,
  type ReviewQueueOrder,
  type SentenceQueueItem,
  type SuggestionDef,
} from '../types/domain'

type UseReaderReviewQueueOptions = {
  activeSuggestions: ComputedRef<SuggestionDef[]>
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  sentenceQueue: Ref<SentenceQueueItem[]>
  setCurrentSentence: (index: number, scrollBehavior?: ScrollBehavior) => void
}

export function useReaderReviewQueue(options: UseReaderReviewQueueOptions) {
  const reviewQueueDetails = ref<ReviewQueueItem[]>([])
  const reviewQueueTotal = ref(0)
  const reviewQueueOrder = ref<ReviewQueueOrder>('hybrid')

  const reviewQueueItems = computed(() => options.sentenceQueue.value.filter((sentence) => !sentence.completed && sentence.suggestion_count > 0))
  const reviewNavigationItems = computed(() => {
    if (reviewQueueOrder.value === 'position' || !reviewQueueDetails.value.length) {
      return reviewQueueItems.value.map((sentence) => ({ id: sentence.id, index: sentence.index }))
    }
    return reviewQueueDetails.value.map((sentence) => ({ id: sentence.id, index: sentence.index }))
  })
  const reviewQueueSummary = computed(() => {
    const items = reviewNavigationItems.value
    const total = reviewQueueOrder.value === 'position' ? reviewQueueItems.value.length : reviewQueueTotal.value || items.length
    if (!total) return 'No review queue'
    const queueIndex = items.findIndex((sentence) => sentence.index === options.currentSentenceIndex.value)
    return queueIndex >= 0 ? `Review ${queueIndex + 1}/${total}` : `${total} pending reviews`
  })
  const hasReviewQueue = computed(() => options.sentenceQueue.value.some((sentence) => !sentence.completed && sentence.suggestion_count > 0))
  const queueItems = computed(() => options.sentenceQueue.value)

  async function refreshReviewQueue() {
    if (!options.documentMeta.value) {
      resetReviewQueueState()
      return
    }
    try {
      const payload = await fetchReviewQueue(PROJECT_ID, options.documentMeta.value.id, 20, reviewQueueOrder.value)
      reviewQueueDetails.value = payload.items
      reviewQueueTotal.value = payload.total
    } catch {
      resetReviewQueueState()
    }
  }

  function setReviewQueueOrder(order: ReviewQueueOrder) {
    if (reviewQueueOrder.value === order) return
    reviewQueueOrder.value = order
    void refreshReviewQueue()
  }

  function jumpToNextReviewSentence() {
    const items = reviewNavigationItems.value
    if (!items.length) return
    const currentQueueIndex = items.findIndex((sentence) => sentence.index === options.currentSentenceIndex.value)
    const target = currentQueueIndex >= 0
      ? items[(currentQueueIndex + 1) % items.length]
      : reviewQueueOrder.value === 'position'
        ? items.find((sentence) => sentence.index >= options.currentSentenceIndex.value + 1) ?? items[0]
        : items[0]
    if (target) options.setCurrentSentence(target.index)
  }

  function jumpToNextReviewIfCurrentCleared() {
    if (options.activeSuggestions.value.length === 0 && hasReviewQueue.value) jumpToNextReviewSentence()
  }

  function resetReviewQueueState() {
    reviewQueueDetails.value = []
    reviewQueueTotal.value = 0
  }

  return {
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
  }
}
