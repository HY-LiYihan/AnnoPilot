import assert from 'node:assert/strict'
import test from 'node:test'

import {
  draftSpansEqual,
  initializeAssistanceDraft,
  replaceOverlappingDraftSpan,
} from '../../src/composables/useReaderAssistance.ts'

const sourceSpans = [
  {
    suggestion_id: 'suggestion-2',
    tag_id: 'verb',
    start_token_index: 4,
    end_token_index: 3,
    start_char: 6,
    end_char: 9,
    text: 'runs',
    confidence: 0.92,
  },
  {
    suggestion_id: 'suggestion-1',
    tag_id: 'noun',
    start_token_index: 0,
    end_token_index: 1,
    start_char: 0,
    end_char: 3,
    text: 'cat',
    confidence: 0.98,
  },
]

test('draft initialization freezes only semantic token ranges in sorted order', () => {
  const draft = initializeAssistanceDraft(sourceSpans)

  assert.deepEqual(draft, [
    { suggestion_id: 'suggestion-1', tag_id: 'noun', start_token_index: 0, end_token_index: 1 },
    { suggestion_id: 'suggestion-2', tag_id: 'verb', start_token_index: 3, end_token_index: 4 },
  ])
  assert.notEqual(draft[0], sourceSpans[1])
})

test('selecting a token range replaces every overlapping local draft span', () => {
  const draft = initializeAssistanceDraft(sourceSpans)
  const replaced = replaceOverlappingDraftSpan(draft, {
    tag_id: 'adjective',
    start_token_index: 1,
    end_token_index: 3,
  })

  assert.deepEqual(replaced, [
    { suggestion_id: null, tag_id: 'adjective', start_token_index: 1, end_token_index: 3 },
  ])
})

test('non-overlapping local spans remain and newly selected ranges are normalized', () => {
  const replaced = replaceOverlappingDraftSpan(initializeAssistanceDraft(sourceSpans), {
    tag_id: 'adjective',
    start_token_index: 7,
    end_token_index: 6,
  })

  assert.deepEqual(replaced.map(({ tag_id, start_token_index, end_token_index }) => ({ tag_id, start_token_index, end_token_index })), [
    { tag_id: 'noun', start_token_index: 0, end_token_index: 1 },
    { tag_id: 'verb', start_token_index: 3, end_token_index: 4 },
    { tag_id: 'adjective', start_token_index: 6, end_token_index: 7 },
  ])
})

test('modified comparison ignores order and suggestion identifiers but detects semantic changes', () => {
  const original = initializeAssistanceDraft(sourceSpans)
  const reordered = [
    { suggestion_id: null, tag_id: 'verb', start_token_index: 3, end_token_index: 4 },
    { suggestion_id: 'new-id', tag_id: 'noun', start_token_index: 0, end_token_index: 1 },
  ]

  assert.equal(draftSpansEqual(original, reordered), true)
  assert.equal(draftSpansEqual(original, replaceOverlappingDraftSpan(original, {
    tag_id: 'adjective',
    start_token_index: 3,
    end_token_index: 4,
  })), false)
})
