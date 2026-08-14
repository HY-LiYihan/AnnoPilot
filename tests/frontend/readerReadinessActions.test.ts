import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildReadinessActions,
  firstPendingSuggestionId,
  firstPendingSuggestionIndex,
  highestPriorityReviewQueueItem,
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
    priority: 50,
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

function makeSuggestion(overrides = {}) {
  return {
    id: 'suggestion-1',
    run_id: 'run-1',
    sentence_id: 'sentence-99',
    tag_id: 'tag-1',
    tag_name: 'Engagement',
    tag_color: '#0b7565',
    start_token_index: 0,
    end_token_index: 1,
    start_char: 0,
    end_char: 4,
    text: 'text',
    confidence: 0.82,
    source: 'lexical_exact',
    status: 'pending',
    created_at: '2026-08-14T00:00:00Z',
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
    [makeReviewQueueItem({ index: 8, first_suggestion: makeSuggestion({ id: 'suggestion-risk' }) })],
  )

  assert.deepEqual(
    actions.map((action) => [
      action.id,
      action.kind,
      action.count,
      action.targetSentenceIndex,
      action.targetSuggestionId ?? null,
      action.targetFocus ?? null,
    ]),
    [
      ['annotation_conflicts', 'review-sentence', 2, 1, null, 'annotation-conflict'],
      ['pending_suggestions', 'review-sentence', 4, 8, 'suggestion-risk', 'suggestion'],
      ['incomplete_sentences', 'review-sentence', 2, 1, null, null],
    ],
  )
})

test('first pending suggestion prefers the highest priority Goldsmith queue item', () => {
  assert.equal(
    firstPendingSuggestionIndex(
      [makeQueueItem({ index: 1, suggestion_count: 2 })],
      [
        makeReviewQueueItem({ index: 7, priority: 50 }),
        makeReviewQueueItem({ index: 4, priority: 108 }),
      ],
    ),
    4,
  )
})

test('first pending suggestion id prefers first suggestion then candidate options', () => {
  assert.equal(firstPendingSuggestionId([makeReviewQueueItem({ priority: 100, first_suggestion: makeSuggestion({ id: 'first' }) })]), 'first')
  assert.equal(firstPendingSuggestionId([makeReviewQueueItem({ priority: 100, candidate_suggestions: [makeSuggestion({ id: 'candidate' })] })]), 'candidate')
})

test('highestPriorityReviewQueueItem breaks ties by risk score then sentence position', () => {
  assert.equal(
    highestPriorityReviewQueueItem([
      makeReviewQueueItem({ id: 'later', index: 9, priority: 70, risk_score: 0.7 }),
      makeReviewQueueItem({ id: 'riskier', index: 8, priority: 70, risk_score: 0.9 }),
      makeReviewQueueItem({ id: 'earlier', index: 3, priority: 70, risk_score: 0.9 }),
    ])?.id,
    'earlier',
  )
})

test('no annotations offers only a manual jump, never silent Monogloss automation', () => {
  const actions = buildReadinessActions(
    documentMeta,
    makeMetrics({ annotation_count: 0, completed_count: 0 }),
    [makeQueueItem({ index: 0, completed: false })],
    [],
  )

  assert.deepEqual(actions.slice(-1).map((action) => [action.id, action.kind, action.targetSentenceIndex]), [
    ['no_annotations', 'review-sentence', 0],
  ])
})
