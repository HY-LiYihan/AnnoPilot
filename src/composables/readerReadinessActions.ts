import type { DocumentMeta, Metrics, ReviewQueueItem, SentenceQueueItem } from '../types/domain'

export type ReadinessActionKind = 'review-sentence'
export type ReadinessTargetFocus = 'annotation-conflict' | 'suggestion'

export type ReadinessActionId =
  | 'annotation_conflicts'
  | 'pending_suggestions'
  | 'incomplete_sentences'
  | 'no_annotations'

export type ReadinessAction = {
  id: ReadinessActionId
  kind: ReadinessActionKind
  count: number
  targetSentenceIndex: number | null
  targetSuggestionId?: string | null
  targetFocus?: ReadinessTargetFocus | null
}

export function pendingSuggestionCount(metrics: Metrics) {
  return metrics.suggestion_status_counts?.pending ?? metrics.suggestion_label_counts.reduce((total, item) => total + item.count, 0)
}

export function annotationOverlapCount(metrics: Metrics) {
  return metrics.annotation_overlap_count ?? 0
}

export function firstAnnotationConflictIndex(queueItems: SentenceQueueItem[]) {
  return queueItems.find((item) => (item.annotation_overlap_count ?? 0) > 0)?.index ?? null
}

export function firstPendingSuggestionIndex(queueItems: SentenceQueueItem[], reviewQueueDetails: ReviewQueueItem[]) {
  return highestPriorityReviewQueueItem(reviewQueueDetails)?.index ?? queueItems.find((item) => item.suggestion_count > 0)?.index ?? null
}

export function firstPendingSuggestionId(reviewQueueDetails: ReviewQueueItem[]) {
  const item = highestPriorityReviewQueueItem(reviewQueueDetails)
  return item?.first_suggestion?.id ?? item?.candidate_suggestions?.[0]?.id ?? null
}

export function highestPriorityReviewQueueItem(reviewQueueDetails: ReviewQueueItem[]) {
  return reviewQueueDetails.reduce<ReviewQueueItem | null>((best, item) => {
    if (!best) return item
    if ((item.priority ?? 0) !== (best.priority ?? 0)) return (item.priority ?? 0) > (best.priority ?? 0) ? item : best
    if ((item.risk_score ?? 0) !== (best.risk_score ?? 0)) return (item.risk_score ?? 0) > (best.risk_score ?? 0) ? item : best
    return item.index < best.index ? item : best
  }, null)
}

export function firstIncompleteSentenceIndex(queueItems: SentenceQueueItem[]) {
  return queueItems.find((item) => !item.completed)?.index ?? null
}

export function buildReadinessActions(
  documentMeta: DocumentMeta | null,
  metrics: Metrics,
  queueItems: SentenceQueueItem[],
  reviewQueueDetails: ReviewQueueItem[],
) {
  if (!documentMeta) return []

  const actions: ReadinessAction[] = []
  const overlapCount = annotationOverlapCount(metrics)
  const suggestionCount = pendingSuggestionCount(metrics)
  const incompleteCount = Math.max(metrics.sentence_count - metrics.completed_count, 0)

  if (overlapCount > 0) {
    actions.push({
      id: 'annotation_conflicts',
      kind: 'review-sentence',
      count: overlapCount,
      targetSentenceIndex: firstAnnotationConflictIndex(queueItems),
      targetFocus: 'annotation-conflict',
    })
  }

  if (suggestionCount > 0) {
    actions.push({
      id: 'pending_suggestions',
      kind: 'review-sentence',
      count: suggestionCount,
      targetSentenceIndex: firstPendingSuggestionIndex(queueItems, reviewQueueDetails),
      targetSuggestionId: firstPendingSuggestionId(reviewQueueDetails),
      targetFocus: 'suggestion',
    })
  }

  if (incompleteCount > 0) {
    actions.push({
      id: 'incomplete_sentences',
      kind: 'review-sentence',
      count: incompleteCount,
      targetSentenceIndex: firstIncompleteSentenceIndex(queueItems),
    })
  }

  if (metrics.sentence_count > 0 && metrics.annotation_count === 0) {
    actions.push({
      id: 'no_annotations',
      kind: 'review-sentence',
      count: 0,
      targetSentenceIndex: queueItems[0]?.index ?? null,
    })
  }

  return actions
}
