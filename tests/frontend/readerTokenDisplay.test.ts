import assert from 'node:assert/strict'
import test from 'node:test'

import {
  annotationForToken,
  suggestionForToken,
  suggestionsWithoutAnnotationOverlaps,
  tokenPrefix,
  tokenStyleForToken,
} from '../../src/composables/readerTokenDisplay.ts'

function makeSentence(overrides = {}) {
  return {
    id: 'sentence-1',
    index: 0,
    text: 'Alpha beta',
    start_char: 0,
    end_char: 10,
    completed: false,
    answer: 'pending',
    tokens: [
      { id: 'token-1', token_index: 0, text: 'Alpha', start_char: 0, end_char: 5 },
      { id: 'token-2', token_index: 1, text: 'beta', start_char: 6, end_char: 10 },
    ],
    annotations: [],
    suggestions: [],
    ...overrides,
  }
}

function makeAnnotation(overrides = {}) {
  return {
    id: 'annotation-1',
    tag_id: 'tag-human',
    tag_name: 'Human',
    tag_color: '#0b7565',
    start_token_index: 0,
    end_token_index: 0,
    start_char: 0,
    end_char: 5,
    text: 'Alpha',
    source: 'human',
    source_suggestion_id: null,
    created_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

function makeSuggestion(overrides = {}) {
  return {
    id: 'suggestion-1',
    run_id: 'run-1',
    sentence_id: 'sentence-1',
    tag_id: 'tag-system',
    tag_name: 'System',
    tag_color: '#326bd8',
    start_token_index: 0,
    end_token_index: 1,
    start_char: 0,
    end_char: 10,
    text: 'Alpha beta',
    confidence: 0.92,
    source: 'lexical_exact',
    status: 'pending',
    created_at: '2026-08-14T00:00:00Z',
    ...overrides,
  }
}

test('token display prefers annotations over suggestions', () => {
  const annotation = makeAnnotation()
  const suggestion = makeSuggestion()
  const sentence = makeSentence({ annotations: [annotation], suggestions: [suggestion] })

  assert.equal(annotationForToken(sentence, 0)?.id, annotation.id)
  assert.equal(suggestionForToken(sentence, 0), undefined)
  assert.deepEqual(tokenStyleForToken(sentence, 0, { activeSuggestion: suggestion }), { '--token-color': '#0b7565' })
})

test('active suggestions, passive suggestions, and pending selections use the expected colors', () => {
  const passiveSuggestion = makeSuggestion({ id: 'suggestion-passive', tag_color: '#326bd8' })
  const activeSuggestion = makeSuggestion({ id: 'suggestion-active', tag_color: '#c45a2e', start_token_index: 1, end_token_index: 1 })
  const sentence = makeSentence({ suggestions: [passiveSuggestion, activeSuggestion] })

  assert.deepEqual(tokenStyleForToken(sentence, 1, { activeSuggestion }), { '--token-color': '#c45a2e' })
  assert.deepEqual(tokenStyleForToken(sentence, 0), { '--token-color': '#326bd8' })
  assert.deepEqual(
    tokenStyleForToken(makeSentence(), 0, {
      selectedTag: { id: 'tag-selected', name: 'Selected', examples: [], shortcut: '1', color: '#7a3db8', count: 0 },
      isTokenPending: () => true,
    }),
    { '--token-color': '#7a3db8' },
  )
})

test('suggestion overlap filtering removes spans covered by annotations', () => {
  const blocked = makeSuggestion({ id: 'blocked', start_token_index: 0, end_token_index: 1 })
  const kept = makeSuggestion({ id: 'kept', start_token_index: 3, end_token_index: 3 })

  assert.deepEqual(
    suggestionsWithoutAnnotationOverlaps([blocked, kept], [makeAnnotation({ start_token_index: 1, end_token_index: 2 })]).map(
      (suggestion) => suggestion.id,
    ),
    ['kept'],
  )
})

test('tokenPrefix preserves whitespace between offset-based tokens', () => {
  const sentence = makeSentence()

  assert.equal(tokenPrefix(sentence, 0), '')
  assert.equal(tokenPrefix(sentence, 1), ' ')
  assert.equal(tokenPrefix(sentence, 99), '')
})

test('tokenPrefix uses backend code-point offsets for non-BMP text', () => {
  const sentence = makeSentence({
    text: '🙂研究员',
    end_char: 4,
    tokens: [
      { id: 'token-emoji', token_index: 0, text: '🙂', start_char: 0, end_char: 1 },
      { id: 'token-researcher', token_index: 1, text: '研究员', start_char: 1, end_char: 4 },
    ],
  })

  assert.equal(tokenPrefix(sentence, 0), '')
  assert.equal(tokenPrefix(sentence, 1), '')
})
