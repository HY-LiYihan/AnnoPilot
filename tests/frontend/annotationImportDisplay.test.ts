import assert from 'node:assert/strict'
import test from 'node:test'

import { annotationImportSkipReasonSummary } from '../../src/composables/annotationImportDisplay.ts'

const labels = {
  importSkipReasonLabels: {
    no_sentence_match: 'sentence not matched',
    invalid_spans: 'invalid spans field',
    invalid_span: 'invalid span boundary',
    unknown: 'unknown reason',
  },
}

test('annotation import skip reasons use stable ordering and localized labels', () => {
  assert.equal(
    annotationImportSkipReasonSummary(
      { invalid_span: 2, no_sentence_match: 1, custom_reason: 3, invalid_spans: 0 },
      labels as any,
    ),
    'sentence not matched 1 · invalid span boundary 2 · custom_reason 3',
  )
})

test('annotation import skip reason summary is empty without skipped records', () => {
  assert.equal(annotationImportSkipReasonSummary({}, labels as any), '')
  assert.equal(annotationImportSkipReasonSummary(undefined, labels as any), '')
})
