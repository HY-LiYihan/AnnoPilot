import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildAnnotationConflictPairs,
  conflictResolutionAnnotation,
  conflictResolutionAnnotationIds,
  firstConflictAnnotationIds,
} from '../../src/composables/readerAnnotationConflicts.ts'

function makeAnnotation(overrides = {}) {
  return {
    id: 'annotation-1',
    tag_id: 'tag-1',
    tag_name: 'Engagement',
    tag_color: '#0b7565',
    start_token_index: 0,
    end_token_index: 0,
    start_char: 0,
    end_char: 2,
    text: '文本',
    source: 'human',
    source_suggestion_id: null,
    created_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

test('annotation conflict pairs include only overlapping token ranges', () => {
  const left = makeAnnotation({ id: 'left', start_token_index: 0, end_token_index: 1 })
  const right = makeAnnotation({ id: 'right', start_token_index: 1, end_token_index: 2 })
  const separate = makeAnnotation({ id: 'separate', start_token_index: 4, end_token_index: 4 })

  const pairs = buildAnnotationConflictPairs([left, right, separate])

  assert.deepEqual(pairs.map((pair) => pair.id), ['left:right'])
})

test('conflict resolution can target the narrower or wider span', () => {
  const narrow = makeAnnotation({ id: 'narrow', start_token_index: 1, end_token_index: 1, start_char: 2, end_char: 4 })
  const wide = makeAnnotation({ id: 'wide', start_token_index: 0, end_token_index: 2, start_char: 0, end_char: 6 })
  const [pair] = buildAnnotationConflictPairs([wide, narrow])

  assert.equal(conflictResolutionAnnotation(pair, 'narrower').id, 'narrow')
  assert.equal(conflictResolutionAnnotation(pair, 'wider').id, 'wide')
})

test('batch conflict resolution ids are unique across chained overlaps', () => {
  const wide = makeAnnotation({ id: 'wide', start_token_index: 0, end_token_index: 4, start_char: 0, end_char: 10 })
  const middle = makeAnnotation({ id: 'middle', start_token_index: 1, end_token_index: 3, start_char: 2, end_char: 8 })
  const narrow = makeAnnotation({ id: 'narrow', start_token_index: 2, end_token_index: 2, start_char: 4, end_char: 6 })
  const pairs = buildAnnotationConflictPairs([wide, middle, narrow])

  assert.deepEqual(conflictResolutionAnnotationIds(pairs, 'narrower'), ['middle', 'narrow'])
  assert.deepEqual(conflictResolutionAnnotationIds(pairs, 'wider'), ['wide', 'middle'])
})

test('firstConflictAnnotationIds returns the first visible conflict target pair', () => {
  const left = makeAnnotation({ id: 'left', start_token_index: 0, end_token_index: 2 })
  const right = makeAnnotation({ id: 'right', start_token_index: 1, end_token_index: 3 })
  const third = makeAnnotation({ id: 'third', start_token_index: 4, end_token_index: 5 })
  const pairs = buildAnnotationConflictPairs([left, right, third])

  assert.deepEqual(firstConflictAnnotationIds(pairs), ['left', 'right'])
  assert.deepEqual(firstConflictAnnotationIds([]), [])
})
