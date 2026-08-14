import assert from 'node:assert/strict'
import test from 'node:test'

import { reviewQueuePriorityRouteText, rosettaRouteLabel } from '../../src/composables/reviewQueueDisplay.ts'

const labels = {
  priority: 'Priority',
  rosettaRoute: 'Route',
  rosettaRouteLabels: {
    low: 'low consistency',
    medium: 'medium consistency',
    high: 'high consistency',
  },
}

function makeReviewQueueItem(overrides = {}) {
  return {
    id: 'sentence-1',
    index: 0,
    text: 'review me',
    suggestion_count: 1,
    priority_score: 0.2,
    priority: 108,
    min_confidence: 0.2,
    lexical_risk_score: 0,
    llm_review_risk_score: 0,
    judge_review_risk_score: 0,
    candidate_disagreement_score: 0,
    risk_score: 0.8,
    risk_reason_codes: [],
    review_route: 'risk',
    rosetta_route: 'low',
    action_hint: '',
    review_guidance: {},
    first_suggestion: null,
    candidate_suggestions: [],
    ...overrides,
  }
}

test('rosettaRouteLabel localizes known route names', () => {
  assert.equal(rosettaRouteLabel('low', labels as any), 'low consistency')
  assert.equal(rosettaRouteLabel('high', labels as any), 'high consistency')
})

test('reviewQueuePriorityRouteText includes priority and Rosetta route', () => {
  assert.equal(
    reviewQueuePriorityRouteText(makeReviewQueueItem(), labels as any),
    'Priority 108 · Route low consistency',
  )
})

test('reviewQueuePriorityRouteText falls back to review guidance route', () => {
  assert.equal(
    reviewQueuePriorityRouteText(makeReviewQueueItem({ rosetta_route: '', review_guidance: { rosetta_route: 'medium' } }), labels as any),
    'Priority 108 · Route medium consistency',
  )
})
