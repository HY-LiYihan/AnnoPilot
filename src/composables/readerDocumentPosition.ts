import type { DocumentSummaryPayload } from '../types/domain'

export function initialSentenceIndex(payload: DocumentSummaryPayload, clampIndex: (index: number) => number) {
  if (typeof payload.session.current_sentence_index === 'number') {
    return clampIndex(payload.session.current_sentence_index)
  }
  return Math.max(
    0,
    payload.queue.find((sentence) => !sentence.completed)?.index ?? 0,
  )
}
