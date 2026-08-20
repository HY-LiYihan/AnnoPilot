import type { AssistanceStatus, SentenceQueueItem } from '../types/domain'

export function overlayAssistanceQueueItems(
  queueItems: readonly SentenceQueueItem[],
  assistanceStatus: AssistanceStatus | null | undefined,
): SentenceQueueItem[] {
  const draftBySentenceId = new Map(
    (assistanceStatus?.queue.items ?? [])
      .filter((item) => item.status === 'ready' || item.status === 'skipped')
      .map((item) => [item.sentence_id, item]),
  )
  if (!draftBySentenceId.size) return [...queueItems]
  return queueItems.map((sentence) => {
    const draft = draftBySentenceId.get(sentence.id)
    if (!draft || sentence.completed || sentence.answer === 'reject' || sentence.answer === 'ignore') return sentence
    return { ...sentence, suggestion_count: Math.max(sentence.suggestion_count, 1) }
  })
}
