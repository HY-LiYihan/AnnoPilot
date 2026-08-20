import assert from 'node:assert/strict'
import test from 'node:test'

import { handleReaderKeyboardShortcut } from '../../src/composables/readerKeyboardDispatch.ts'

function makeTag(overrides = {}) {
  return {
    id: 'tag-noun',
    name: 'Noun',
    description: null,
    examples: [],
    shortcut: '1',
    color: '#2563eb',
    count: 0,
    usage_count: 0,
    suggestion_count: 0,
    ...overrides,
  }
}

function makeSuggestion(overrides = {}) {
  return {
    id: 'suggestion-1',
    run_id: 'run-1',
    sentence_id: 'sentence-1',
    tag_id: 'tag-noun',
    tag_name: 'Noun',
    tag_color: '#2563eb',
    start_token_index: 0,
    end_token_index: 0,
    start_char: 0,
    end_char: 2,
    text: '市场',
    confidence: 0.9,
    source: 'test',
    status: 'pending',
    created_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

function makeTarget(kind) {
  return {
    isContentEditable: kind === 'editable',
    matches(selector) {
      if (kind === 'input') return selector === 'input, textarea, select'
      if (kind === 'button') return selector === 'button, a'
      return false
    },
  }
}

function makeOptions(overrides = {}) {
  const calls = []
  const suggestion = makeSuggestion()
  const options = {
    activeSuggestion: { value: suggestion },
    activeSuggestions: { value: [suggestion] },
    currentSentenceIndex: { value: 4 },
    tags: { value: [makeTag(), makeTag({ id: 'tag-verb', name: 'Verb', shortcut: '2' })] },
    acceptCurrentSentenceSuggestions: () => calls.push(['accept-sentence']),
    acceptSuggestedSpan: (item) => calls.push(['accept-suggestion', item.id]),
    applyTagToSelection: (tagId) => calls.push(['apply-tag', tagId]),
    completeCurrentSentence: (answer) => calls.push(['complete', answer]),
    cycleActiveSuggestionTarget: (direction) => calls.push(['cycle-suggestion', direction]),
    jumpToNextReviewSentence: () => calls.push(['jump-review']),
    markCurrentSentenceMonogloss: () => calls.push(['mark-monogloss']),
    rejectCurrentSentenceSuggestions: () => calls.push(['reject-sentence']),
    rejectSuggestedSpan: (item) => calls.push(['reject-suggestion', item.id]),
    reopenCurrentSentence: () => calls.push(['reopen']),
    selectCurrentSentenceSpan: () => calls.push(['select-sentence']),
    setCurrentSentence: (index) => calls.push(['set-sentence', index]),
    undoLastSpanAction: () => calls.push(['undo']),
    ...overrides,
  }
  return { calls, options }
}

function dispatch(key, eventOverrides = {}, optionOverrides = {}) {
  const { calls, options } = makeOptions(optionOverrides)
  let prevented = false
  const event = {
    key,
    code: key === ' ' ? 'Space' : '',
    preventDefault: () => {
      prevented = true
    },
    target: null,
    ...eventOverrides,
  }
  const handled = handleReaderKeyboardShortcut(event, options)
  return { calls, handled, prevented }
}

test('number shortcuts apply the matching tag to the selected span', () => {
  const result = dispatch('2')

  assert.equal(result.handled, true)
  assert.equal(result.prevented, true)
  assert.deepEqual(result.calls, [['apply-tag', 'tag-verb']])
})

test('sentence completion and span shortcuts dispatch the expected actions', () => {
  assert.deepEqual(dispatch('Enter').calls, [['complete', undefined]])
  assert.deepEqual(dispatch('i').calls, [['complete', 'ignore']])
  assert.deepEqual(dispatch('j').calls, [['complete', 'reject']])
  assert.deepEqual(dispatch('e').calls, [['reopen']])
  assert.deepEqual(dispatch('s').calls, [['select-sentence']])
  assert.deepEqual(dispatch('m').calls, [['mark-monogloss']])
  assert.deepEqual(dispatch(' ').calls, [['complete', 'ignore']])
})

test('space removes the hovered annotation before applying sentence shortcuts', () => {
  const removed = []
  const result = dispatch(' ', {}, {
    hoveredAnnotationId: { value: 'annotation-1' },
    removeHoveredAnnotation: () => removed.push('annotation-1'),
  })

  assert.equal(result.handled, true)
  assert.equal(result.prevented, true)
  assert.deepEqual(removed, ['annotation-1'])
  assert.deepEqual(result.calls, [])
})

test('navigation shortcuts move relative to the active sentence', () => {
  assert.deepEqual(dispatch('ArrowDown').calls, [['set-sentence', 5]])
  assert.deepEqual(dispatch('ArrowUp').calls, [['set-sentence', 3]])
})

test('suggestion shortcuts cycle, accept, and reject suggestions', () => {
  assert.deepEqual(dispatch('Tab').calls, [['cycle-suggestion', 1]])
  assert.deepEqual(dispatch('Tab', { shiftKey: true }).calls, [['cycle-suggestion', -1]])
  assert.deepEqual(dispatch('a').calls, [['accept-sentence']])
  assert.deepEqual(dispatch('x').calls, [['reject-sentence']])
  assert.deepEqual(dispatch('y').calls, [['accept-suggestion', 'suggestion-1']])
  assert.deepEqual(dispatch('n').calls, [['reject-suggestion', 'suggestion-1']])
  assert.deepEqual(dispatch('r').calls, [['jump-review']])
})

test('assistance draft mode keeps only labels, confirm, skip, and navigation shortcuts', () => {
  const draftActive = { assistanceDraftActive: { value: true } }

  assert.deepEqual(dispatch('1', {}, draftActive).calls, [['apply-tag', 'tag-noun']])
  assert.deepEqual(dispatch('Enter', {}, draftActive).calls, [['complete', undefined]])
  assert.deepEqual(dispatch(' ', {}, draftActive).calls, [['complete', 'ignore']])
  assert.deepEqual(dispatch('ArrowDown', {}, draftActive).calls, [['set-sentence', 5]])

  for (const key of ['Tab', 'a', 'x', 'y', 'n', 'r', 'm', 's', 'e']) {
    const result = dispatch(key, {}, draftActive)
    assert.equal(result.handled, false)
    assert.deepEqual(result.calls, [])
  }

  const undo = dispatch('z', { ctrlKey: true }, draftActive)
  assert.equal(undo.handled, false)
  assert.deepEqual(undo.calls, [])
})

test('shortcuts are ignored while typing or using ordinary modifier chords', () => {
  const inputResult = dispatch('1', { target: makeTarget('input') })
  assert.equal(inputResult.handled, false)
  assert.equal(inputResult.prevented, false)
  assert.deepEqual(inputResult.calls, [])

  const editableResult = dispatch('Enter', { target: makeTarget('editable') })
  assert.equal(editableResult.handled, false)
  assert.equal(editableResult.prevented, false)
  assert.deepEqual(editableResult.calls, [])

  const modifierResult = dispatch('1', { altKey: true })
  assert.equal(modifierResult.handled, false)
  assert.equal(modifierResult.prevented, false)
  assert.deepEqual(modifierResult.calls, [])
})

test('ctrl/cmd z remains available for undo and space does not override buttons', () => {
  assert.deepEqual(dispatch('z', { ctrlKey: true }).calls, [['undo']])
  assert.deepEqual(dispatch('z', { metaKey: true }).calls, [['undo']])

  const buttonSpace = dispatch(' ', { code: 'Space', target: makeTarget('button') })
  assert.equal(buttonSpace.handled, false)
  assert.equal(buttonSpace.prevented, false)
  assert.deepEqual(buttonSpace.calls, [])

  const buttonEnter = dispatch('Enter', { target: makeTarget('button') })
  assert.equal(buttonEnter.handled, false)
  assert.equal(buttonEnter.prevented, false)
  assert.deepEqual(buttonEnter.calls, [])

  const buttonShortcut = dispatch('1', { target: makeTarget('button') })
  assert.equal(buttonShortcut.handled, false)
  assert.equal(buttonShortcut.prevented, false)
  assert.deepEqual(buttonShortcut.calls, [])
})
