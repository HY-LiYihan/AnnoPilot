import type { DocumentMeta, Metrics, ReviewQueueItem, SentenceQueueItem } from '../types/domain'

export type ReadinessActionKind = 'review-sentence' | 'auto-mark-monogloss'
export type ReadinessTargetFocus = 'annotation-conflict' | 'suggestion'

export type ReadinessActionId =
  | 'annotation_conflicts'
  | 'pending_suggestions'
  | 'incomplete_sentences'
  | 'no_annotations'
  | 'auto_monogloss'

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
  return reviewQueueDetails[0]?.index ?? queueItems.find((item) => item.suggestion_count > 0)?.index ?? null
}

export function firstPendingSuggestionId(reviewQueueDetails: ReviewQueueItem[]) {
  return reviewQueueDetails[0]?.first_suggestion?.id ?? reviewQueueDetails[0]?.candidate_suggestions?.[0]?.id ?? null
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
    actions.push({
      id: 'auto_monogloss',
      kind: 'auto-mark-monogloss',
      count: incompleteCount,
      targetSentenceIndex: null,
    })
  }

  return actions
}
