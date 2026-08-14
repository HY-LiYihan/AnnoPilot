import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildReadinessActions,
  firstPendingSuggestionIndex,
  pendingSuggestionCount,
} from '../../src/composables/readerReadinessActions.ts'

function makeMetrics(overrides = {}) {
  return {
    sentence_count: 3,
    completed_count: 3,
    answer_counts: { accept: 3 },
    progress: 1,
    annotation_count: 2,
    annotation_overlap_count: 0,
    suggestion_count: 0,
    annotation_label_counts: [],
    suggestion_label_counts: [],
    suggestion_status_counts: {},
    suggestion_source_counts: {},
    suggestion_confidence_counts: {},
    suggestion_review_counts: {},
    reviewed_suggestion_count: 0,
    accuracy: null,
    accuracy_label: 'Waiting for review data',
    calibration_count: 0,
    calibration_disagreement_count: 0,
    calibration_error_rate: null,
    review_efficiency_curves: {},
    ...overrides,
  }
}

function makeQueueItem(overrides = {}) {
  return {
    id: 'sentence-1',
    index: 0,
    completed: true,
    answer: 'accept',
    suggestion_count: 0,
    annotation_overlap_count: 0,
    ...overrides,
  }
}

function makeReviewQueueItem(overrides = {}) {
  return {
    id: 'sentence-99',
    index: 99,
    text: 'review me',
    suggestion_count: 1,
    priority_score: 0.2,
    min_confidence: 0.2,
    lexical_risk_score: 0,
    llm_review_risk_score: 0,
    judge_review_risk_score: 0,
    candidate_disagreement_score: 0,
    risk_score: 0.8,
    risk_reason_codes: [],
    review_route: 'risk',
    action_hint: '',
    review_guidance: {},
    first_suggestion: null,
    candidate_suggestions: [],
    ...overrides,
  }
}

const documentMeta = { id: 'doc-1', filename: 'sample.txt', sentence_count: 3, token_count: 9 }

test('pendingSuggestionCount falls back to label counts when status counts are absent', () => {
  const metrics = makeMetrics({
    suggestion_label_counts: [
      { tag_id: 'a', name: 'A', color: '#000000', count: 2 },
      { tag_id: 'b', name: 'B', color: '#111111', count: 3 },
    ],
  })

  assert.equal(pendingSuggestionCount(metrics), 5)
})

test('readiness actions surface conflicts, pending suggestions, and incomplete sentences', () => {
  const actions = buildReadinessActions(
    documentMeta,
    makeMetrics({ completed_count: 1, annotation_overlap_count: 2, suggestion_status_counts: { pending: 4 } }),
    [
      makeQueueItem({ id: 's1', index: 0, completed: true, annotation_overlap_count: 0, suggestion_count: 0 }),
      makeQueueItem({ id: 's2', index: 1, completed: false, annotation_overlap_count: 2, suggestion_count: 1 }),
    ],
    [makeReviewQueueItem({ index: 8 })],
  )

  assert.deepEqual(
    actions.map((action) => [action.id, action.kind, action.count, action.targetSentenceIndex]),
    [
      ['annotation_conflicts', 'review-sentence', 2, 1],
      ['pending_suggestions', 'review-sentence', 4, 8],
      ['incomplete_sentences', 'review-sentence', 2, 1],
    ],
  )
})

test('first pending suggestion prefers the Goldsmith review queue order', () => {
  assert.equal(
    firstPendingSuggestionIndex(
      [makeQueueItem({ index: 1, suggestion_count: 2 })],
      [makeReviewQueueItem({ index: 7 })],
    ),
    7,
  )
})

test('no annotations offers both manual jump and Monogloss automation', () => {
  const actions = buildReadinessActions(
    documentMeta,
    makeMetrics({ annotation_count: 0, completed_count: 0 }),
    [makeQueueItem({ index: 0, completed: false })],
    [],
  )

  assert.deepEqual(actions.slice(-2).map((action) => [action.id, action.kind, action.targetSentenceIndex]), [
    ['no_annotations', 'review-sentence', 0],
    ['auto_monogloss', 'auto-mark-monogloss', null],
  ])
})
