import assert from 'node:assert/strict'
import test from 'node:test'

import { initialSentenceIndex } from '../../src/composables/readerDocumentPosition.ts'

function makeSummary(overrides = {}) {
  return {
    document: { id: 'doc-1', filename: 'sample.txt', sentence_count: 3, token_count: 9 },
    session: { id: 'session-1', actor_id: 'annopilot-human', current_sentence_index: null, updated_at: null },
    tags: [],
    metrics: {
      sentence_count: 3,
      completed_count: 0,
      answer_counts: { pending: 3 },
      progress: 0,
      annotation_count: 0,
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
    },
    queue: [
      { id: 'sentence-1', index: 0, completed: true, answer: 'accept', suggestion_count: 0 },
      { id: 'sentence-2', index: 1, completed: false, answer: 'pending', suggestion_count: 0 },
      { id: 'sentence-3', index: 2, completed: false, answer: 'pending', suggestion_count: 0 },
    ],
    ...overrides,
  }
}

test('initial sentence restores persisted cursor through the current clamp rule', () => {
  const payload = makeSummary({
    session: { id: 'session-1', actor_id: 'annopilot-human', current_sentence_index: 99, updated_at: null },
  })

  assert.equal(initialSentenceIndex(payload, () => 2), 2)
})

test('initial sentence falls back to the first incomplete queue item', () => {
  assert.equal(initialSentenceIndex(makeSummary(), (index) => index), 1)
})

test('initial sentence starts at zero when every sentence is completed', () => {
  const payload = makeSummary({
    queue: [
      { id: 'sentence-1', index: 0, completed: true, answer: 'accept', suggestion_count: 0 },
      { id: 'sentence-2', index: 1, completed: true, answer: 'accept', suggestion_count: 0 },
    ],
  })

  assert.equal(initialSentenceIndex(payload, (index) => index), 0)
})
