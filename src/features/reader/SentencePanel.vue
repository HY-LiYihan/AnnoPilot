<script setup lang="ts">
import { ref } from 'vue'
import { Check, Clock, FileText, Sparkles, Undo2, Upload, X } from '@lucide/vue'
import type { AnnotationDef, DocumentMeta, DragSelection, SentenceDef, SuggestionDef, SuggestionReview } from '../../types/domain'

const SWIPE_MIN_DISTANCE = 56
const SWIPE_AXIS_RATIO = 1.4

defineProps<{
  documentMeta: DocumentMeta | null
  currentSentence: SentenceDef | null
  currentSentenceIndex: number
  sentences: SentenceDef[]
  activeAnnotations: AnnotationDef[]
  activeSuggestions: SuggestionDef[]
  canUndoSpanAction: boolean
  undoLabel: string
  pendingSelection: DragSelection | null
  pendingSelectionText: string
  hasReviewQueue: boolean
  reviewQueueSummary: string
  readerError: string
  isUploading: boolean
  isSaving: boolean
  isSuggesting: boolean
  suggestionLimit: number
  suggestionMinConfidence: number
  suggestionReviews: Record<string, SuggestionReview>
  reviewingSuggestionId: string
  annotationForToken: (sentence: SentenceDef, tokenIndex: number) => AnnotationDef | undefined
  suggestionForToken: (sentence: SentenceDef, tokenIndex: number) => SuggestionDef | undefined
  isTokenInDrag: (sentence: SentenceDef, tokenIndex: number) => boolean
  isTokenPending: (sentence: SentenceDef, tokenIndex: number) => boolean
  tokenPrefix: (sentence: SentenceDef, tokenIndex: number) => string
  tokenStyle: (sentence: SentenceDef, tokenIndex: number) => Record<string, string>
}>()

const emit = defineEmits<{
  import: [event: Event]
  'set-sentence-element': [sentenceId: string, element: unknown]
  'sentence-click': [sentenceIndex: number]
  'token-pointer-down': [sentence: SentenceDef, tokenIndex: number, event: PointerEvent]
  'token-pointer-enter': [sentence: SentenceDef, tokenIndex: number]
  'token-pointer-up': [sentence: SentenceDef, tokenIndex: number]
  'delete-annotation': [annotationId: string]
  undo: []
  'generate-current-suggestions': []
  'generate-suggestions': []
  'accept-suggestion': [suggestion: SuggestionDef]
  'reject-suggestion': [suggestion: SuggestionDef]
  'accept-current-suggestions': []
  'auto-accept-document-suggestions': []
  'reject-current-suggestions': []
  'auto-reject-document-suggestions': []
  'review-suggestion': [suggestion: SuggestionDef]
  'suggestion-limit-change': [limit: number]
  'suggestion-min-confidence-change': [minConfidence: number]
  'next-review': []
  complete: []
  ignore: []
  previous: []
  next: []
}>()

function handleSuggestionLimitInput(event: Event) {
  const input = event.target as HTMLInputElement
  emit('suggestion-limit-change', Number(input.value))
}

function handleSuggestionMinConfidenceInput(event: Event) {
  const input = event.target as HTMLInputElement
  emit('suggestion-min-confidence-change', Number(input.value) / 100)
}

const swipeStart = ref<{ x: number; y: number; pointerId: number } | null>(null)

function isInteractiveTarget(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.closest('button, input, select, textarea, label, a, [role="button"]'))
}

function handleReaderPointerDown(event: PointerEvent) {
  if (event.pointerType !== 'touch' || isInteractiveTarget(event.target)) return
  swipeStart.value = { x: event.clientX, y: event.clientY, pointerId: event.pointerId }
}

function handleReaderPointerUp(event: PointerEvent) {
  const start = swipeStart.value
  if (!start || start.pointerId !== event.pointerId) return
  swipeStart.value = null
  const deltaX = event.clientX - start.x
  const deltaY = event.clientY - start.y
  if (Math.abs(deltaX) < SWIPE_MIN_DISTANCE || Math.abs(deltaX) < Math.abs(deltaY) * SWIPE_AXIS_RATIO) return
  if (deltaX < 0) {
    emit('next')
  } else {
    emit('previous')
  }
}

function clearReaderSwipe() {
  swipeStart.value = null
}

function suggestionSourceLabel(source: string) {
  const labels: Record<string, string> = {
    lexical_exact: 'Exact',
    lexical_contains: 'Contains',
    char_ngram: 'Char n-gram',
  }
  return labels[source] ?? source
}

function suggestionRangeLabel(suggestion: SuggestionDef) {
  const tokenRange =
    suggestion.start_token_index === suggestion.end_token_index
      ? `tok ${suggestion.start_token_index}`
      : `tok ${suggestion.start_token_index}-${suggestion.end_token_index}`
  return `${tokenRange} · char ${suggestion.start_char}-${suggestion.end_char}`
}
</script>

<template>
  <section class="editor-panel" aria-labelledby="editor-title">
    <div class="editor-header">
      <div>
        <p class="section-kicker">Annotation Reader</p>
        <h1 id="editor-title">{{ documentMeta?.filename ?? 'Import a TXT file to begin' }}</h1>
      </div>
      <div class="editor-tools">
        <label class="upload-card editor-upload">
          <Upload :size="19" aria-hidden="true" />
          <span>
            <strong>{{ isUploading ? 'Importing...' : 'Import TXT' }}</strong>
            <small>UTF-8 · 10 MB</small>
          </span>
          <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="emit('import', $event)" />
        </label>
        <div class="document-pill">
          <Clock :size="16" aria-hidden="true" />
          <span>{{ currentSentence ? `Sentence ${currentSentence.index + 1}` : 'No document' }}</span>
        </div>
      </div>
    </div>

    <p v-if="readerError" class="reader-error">{{ readerError }}</p>

    <article v-if="!documentMeta" class="empty-reader" aria-label="Empty reader state">
      <FileText :size="44" aria-hidden="true" />
      <h2>Import a TXT file</h2>
      <p>
        AnnoPilot will split it into sentences, tokenize each sentence, and keep annotation progress in SQLite
        while writing audit events to JSONL.
      </p>
      <label class="upload-card empty-upload">
        <Upload :size="22" aria-hidden="true" />
        <span>
          <strong>{{ isUploading ? 'Importing TXT...' : 'Choose TXT from Downloads' }}</strong>
          <small>Pick your prepared test file</small>
        </span>
        <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="emit('import', $event)" />
      </label>
    </article>

    <article
      v-else
      class="text-reader"
      aria-label="Scrollable text annotation reader"
      @pointerdown="handleReaderPointerDown"
      @pointerup="handleReaderPointerUp"
      @pointercancel="clearReaderSwipe"
      @pointerleave="clearReaderSwipe"
    >
      <section
        v-for="sentence in sentences"
        :key="sentence.id"
        :ref="(element) => emit('set-sentence-element', sentence.id, element)"
        class="sentence-card"
        :class="{
          active: sentence.index === currentSentenceIndex,
          dimmed: sentence.index !== currentSentenceIndex,
          completed: sentence.completed,
          rejected: sentence.answer === 'reject',
          ignored: sentence.answer === 'ignore',
          'needs-review': !sentence.completed && sentence.suggestions.length > 0,
        }"
        @click="emit('sentence-click', sentence.index)"
      >
        <div class="sentence-meta">
          <span>#{{ sentence.index + 1 }}</span>
          <strong>{{ sentence.answer === 'reject' ? 'Rejected' : sentence.answer === 'ignore' ? 'Ignored' : sentence.completed ? 'Completed' : sentence.suggestions.length ? 'Review' : sentence.index === currentSentenceIndex ? 'Active' : 'Waiting' }}</strong>
        </div>
        <p class="sentence-text">
          <template v-for="(token, tokenIndex) in sentence.tokens" :key="token.id">
            {{ tokenPrefix(sentence, tokenIndex) }}<button
              class="token"
              :class="{
                annotated: annotationForToken(sentence, token.token_index),
                suggested: suggestionForToken(sentence, token.token_index),
                selecting: isTokenInDrag(sentence, token.token_index),
                pending: isTokenPending(sentence, token.token_index),
              }"
              :style="tokenStyle(sentence, token.token_index)"
              @pointerdown="emit('token-pointer-down', sentence, token.token_index, $event)"
              @pointerenter="emit('token-pointer-enter', sentence, token.token_index)"
              @pointerup="emit('token-pointer-up', sentence, token.token_index)"
            >{{ token.text }}</button>
          </template>
        </p>
      </section>
    </article>

    <section class="candidate-card" aria-labelledby="candidate-title">
      <div class="candidate-heading">
        <div>
          <h2 id="candidate-title">Current sentence spans</h2>
          <span>{{ activeAnnotations.length }} selected · {{ activeSuggestions.length }} suggested</span>
        </div>
        <div class="candidate-actions">
          <label class="suggest-limit-control" title="每句最多生成多少条候选建议">
            <span>Limit</span>
            <input
              type="number"
              min="1"
              max="20"
              step="1"
              :value="suggestionLimit"
              :disabled="isSuggesting"
              @input="handleSuggestionLimitInput"
            />
          </label>
          <label class="suggest-limit-control" title="候选建议的最低置信度">
            <span>Min</span>
            <input
              type="number"
              min="0"
              max="100"
              step="5"
              :value="Math.round(suggestionMinConfidence * 100)"
              :disabled="isSuggesting"
              @input="handleSuggestionMinConfidenceInput"
            />
            <span>%</span>
          </label>
          <button class="suggest-button" :disabled="!currentSentence || isSuggesting" @click="emit('generate-current-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ isSuggesting ? 'Suggesting...' : 'Suggest current' }}
          </button>
          <button class="suggest-button secondary" :disabled="!documentMeta || isSuggesting" @click="emit('generate-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            Whole doc
          </button>
          <button class="batch-review-button accept" :disabled="!activeSuggestions.length || isSaving" @click="emit('accept-current-suggestions')">
            <Check :size="16" aria-hidden="true" />
            Accept all · A
          </button>
          <button class="batch-review-button accept" :disabled="!documentMeta || !hasReviewQueue || isSaving" @click="emit('auto-accept-document-suggestions')">
            <Check :size="16" aria-hidden="true" />
            Accept ≥ Min
          </button>
          <button class="batch-review-button reject" :disabled="!activeSuggestions.length || isSaving" @click="emit('reject-current-suggestions')">
            <X :size="16" aria-hidden="true" />
            Reject all · X
          </button>
          <button class="batch-review-button reject" :disabled="!documentMeta || !hasReviewQueue || isSaving" @click="emit('auto-reject-document-suggestions')">
            <X :size="16" aria-hidden="true" />
            Reject pending
          </button>
          <button class="review-button compact" :disabled="!hasReviewQueue" @click="emit('next-review')">
            {{ reviewQueueSummary }} · R
          </button>
        </div>
      </div>
      <div v-if="pendingSelection && pendingSelectionText" class="pending-card">
        <span>
          <strong>{{ pendingSelectionText }}</strong>
          <small>Press 1-9 or click a tag on the left to apply</small>
        </span>
        <em>Pending</em>
      </div>
      <div v-if="activeAnnotations.length" class="candidate-list">
        <button
          v-for="annotation in activeAnnotations"
          :key="annotation.id"
          class="candidate-row"
          :style="{ '--token-color': annotation.tag_color }"
          @click="emit('delete-annotation', annotation.id)"
        >
          <span>
            <strong>{{ annotation.text }}</strong>
            <small>
              {{ annotation.tag_name }}
              <em v-if="annotation.source === 'accepted_suggestion'" class="source-badge">AI accepted</em>
            </small>
          </span>
          <em>Remove</em>
        </button>
      </div>
      <div v-if="activeSuggestions.length" class="suggestion-list" aria-label="Low-compute RAG suggestions">
        <article
          v-for="(suggestion, suggestionIndex) in activeSuggestions"
          :key="suggestion.id"
          class="suggestion-row"
          :class="{ 'keyboard-target': suggestionIndex === 0 }"
          :style="{ '--token-color': suggestion.tag_color }"
        >
          <span>
            <strong>{{ suggestion.text }}</strong>
            <small class="suggestion-meta-line">
              <em class="suggestion-badge">{{ suggestionSourceLabel(suggestion.source) }}</em>
              <em>{{ suggestion.tag_name }}</em>
              <em>{{ Math.round(suggestion.confidence * 100) }}%</em>
              <em>{{ suggestionRangeLabel(suggestion) }}</em>
              <em v-if="suggestion.run_id">{{ suggestion.run_id.slice(0, 10) }}</em>
              <em v-if="suggestionIndex === 0" class="keyboard-target-badge">Y/N target</em>
            </small>
            <small v-if="suggestion.evidence_text" class="evidence-copy">
              <em>Evidence</em>
              <strong>{{ suggestion.evidence_text }}</strong>
            </small>
            <small v-if="suggestion.context_before || suggestion.context_after" class="evidence-copy">
              <em>Context</em>
              <strong>{{ suggestion.context_before }}[{{ suggestion.text }}]{{ suggestion.context_after }}</strong>
            </small>
            <small v-if="suggestionReviews[suggestion.id]" class="review-copy">
              <em>LLM review</em>
              {{ suggestionReviews[suggestion.id].recommendation }} ·
              {{ Math.round(suggestionReviews[suggestion.id].confidence * 100) }}% ·
              {{ suggestionReviews[suggestion.id].rationale }}
            </small>
          </span>
          <div class="suggestion-actions">
            <button
              type="button"
              :disabled="isSaving || reviewingSuggestionId === suggestion.id"
              title="让 gpt5.5 评审候选"
              @click="emit('review-suggestion', suggestion)"
            >
              <Sparkles :size="15" aria-hidden="true" />
            </button>
            <button type="button" :disabled="isSaving" title="接受建议" @click="emit('accept-suggestion', suggestion)">
              <Check :size="15" aria-hidden="true" />
            </button>
            <button type="button" :disabled="isSaving" title="拒绝建议" @click="emit('reject-suggestion', suggestion)">
              <X :size="15" aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
      <p v-if="!activeAnnotations.length && !activeSuggestions.length" class="candidate-empty">
        Select a word or span first, then press 1-9 or click a tag to label it. Or run low-compute suggestions.
      </p>
    </section>

    <section class="verification-card" aria-label="Human verification actions">
      <div>
        <p class="section-kicker">Human Verification</p>
        <h2>Complete sentence and continue</h2>
      </div>
      <div class="verification-actions">
        <button class="accept-button" :disabled="!currentSentence || isSaving" @click="emit('complete')">
          Complete · Enter
        </button>
        <button class="edit-button" :disabled="!currentSentence" @click="emit('previous')">Previous</button>
        <button class="skip-button" :disabled="!currentSentence || isSaving" @click="emit('ignore')">Ignore · Space / I</button>
        <button class="undo-button" :disabled="!canUndoSpanAction || isSaving" :title="undoLabel" @click="emit('undo')">
          <Undo2 :size="16" aria-hidden="true" />
          Undo · ⌘Z
        </button>
        <button class="review-button" :disabled="!hasReviewQueue" @click="emit('next-review')">{{ reviewQueueSummary }}</button>
      </div>
    </section>
  </section>
</template>
