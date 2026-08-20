import assert from 'node:assert/strict'
import test from 'node:test'

import { overlayAssistanceQueueItems } from '../../src/composables/readerQueueDisplay.ts'

function sentence(id: string, index: number, overrides = {}) {
  return {
    id,
    index,
    completed: false,
    answer: 'pending',
    suggestion_count: 0,
    annotation_overlap_count: 0,
    ...overrides,
  }
}

function status(items) {
  return {
    enabled: true,
    seed_per_tag: 5,
    concurrency: 10,
    knowledge_revision: 1,
    active_tags: [],
    tag_progress: [],
    usage: {},
    queue: {
      counts: {},
      ready: 1,
      running: 1,
      queued: 1,
      skipped: 1,
      failed: 0,
      items,
    },
  }
}

function draft(sentenceId: string, statusValue: string) {
  return {
    id: `${sentenceId}-${statusValue}`,
    draft_id: `${sentenceId}-${statusValue}`,
    draft_version: 1,
    document_id: 'doc-1',
    sentence_id: sentenceId,
    sentence_index: 0,
    sentence_text: '',
    status: statusValue,
    queue_order: 1,
    knowledge_revision: 1,
    active_tag_ids: [],
    verifier_issues: [],
    attempt_count: 0,
    usage: {},
    spans: [],
  }
}

test('assistance queue overlay marks ready and skipped drafts as pending review only', () => {
  const overlaid = overlayAssistanceQueueItems(
    [sentence('ready', 0), sentence('skipped', 1), sentence('running', 2), sentence('queued', 3)],
    status([draft('ready', 'ready'), draft('skipped', 'skipped'), draft('running', 'running'), draft('queued', 'queued')]),
  )

  assert.equal(overlaid[0].suggestion_count, 1)
  assert.equal(overlaid[1].suggestion_count, 1)
  assert.equal(overlaid[2].suggestion_count, 0)
  assert.equal(overlaid[3].suggestion_count, 0)
})

test('assistance queue overlay never changes completed or rejected sentences', () => {
  const overlaid = overlayAssistanceQueueItems(
    [sentence('done', 0, { completed: true }), sentence('rejected', 1, { answer: 'reject' })],
    status([draft('done', 'ready'), draft('rejected', 'ready')]),
  )

  assert.equal(overlaid[0].suggestion_count, 0)
  assert.equal(overlaid[1].suggestion_count, 0)
})
