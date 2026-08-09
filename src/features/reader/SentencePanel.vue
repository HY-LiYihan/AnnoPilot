<script setup lang="ts">
import { ref } from 'vue'
import { Check, Sparkles, Undo2, X } from '@lucide/vue'
import type { UiLabels } from '../../i18n'
import type {
  AnnotationDef,
  DocumentListItem,
  DocumentMeta,
  DragSelection,
  SentenceDef,
  SuggestionDef,
  SuggestionReview,
  TxtImportMode,
} from '../../types/domain'

const SWIPE_MIN_DISTANCE = 56
const SWIPE_AXIS_RATIO = 1.4

const props = defineProps<{
  labels: UiLabels['reader']
  documentMeta: DocumentMeta | null
  documents: DocumentListItem[]
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
  import: [file: File, mode: TxtImportMode]
  'document-change': [documentId: string]
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
  reject: []
  reopen: []
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

function handleDocumentChange(event: Event) {
  const select = event.target as HTMLSelectElement
  emit('document-change', select.value)
}

function handleFileInput(event: Event, mode: TxtImportMode) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('import', file, mode)
  input.value = ''
}

const isDraggingTxt = ref(false)

function hasFileDrag(event: DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

function handleDragEnter(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  isDraggingTxt.value = true
}

function handleDragOver(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  isDraggingTxt.value = true
}

function handleDragLeave(event: DragEvent) {
  const currentTarget = event.currentTarget
  const relatedTarget = event.relatedTarget
  if (currentTarget instanceof Node && relatedTarget instanceof Node && currentTarget.contains(relatedTarget)) return
  isDraggingTxt.value = false
}

function handleDrop(event: DragEvent, mode: TxtImportMode) {
  event.preventDefault()
  isDraggingTxt.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  const txtFile = files.find((file) => file.name.toLowerCase().endsWith('.txt') || file.type === 'text/plain')
  if (txtFile) emit('import', txtFile, mode)
}

function documentOptionText(document: DocumentListItem, labels: UiLabels['reader']) {
  const progress = Math.round(document.progress * 100)
  const cursor = typeof document.current_sentence_index === 'number' ? labels.cursor(document.current_sentence_index + 1) : ''
  return labels.documentOption(document.filename, cursor, progress, document.annotation_count, document.suggestion_count)
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

function suggestionSourceLabel(source: string, labels: UiLabels['reader']) {
  return labels.sourceLabels[source as keyof typeof labels.sourceLabels] ?? source
}

function suggestionRangeLabel(suggestion: SuggestionDef, labels: UiLabels['reader']) {
  const tokenRange =
    suggestion.start_token_index === suggestion.end_token_index
      ? `${labels.tokenRange} ${suggestion.start_token_index}`
      : `${labels.tokenRange} ${suggestion.start_token_index}-${suggestion.end_token_index}`
  return `${tokenRange} · ${labels.charRange} ${suggestion.start_char}-${suggestion.end_char}`
}

function sentenceStatusLabel(sentence: SentenceDef, currentSentenceIndex: number, labels: UiLabels['reader']) {
  if (sentence.answer === 'reject') return labels.statusRejected
  if (sentence.answer === 'ignore') return labels.statusIgnored
  if (sentence.completed) return labels.statusCompleted
  if (sentence.suggestions.length) return labels.statusReview
  if (sentence.index === currentSentenceIndex) return labels.statusActive
  return labels.statusWaiting
}

function tokenSpanClasses(sentence: SentenceDef, tokenIndex: number) {
  const annotation = props.annotationForToken(sentence, tokenIndex)
  if (annotation) return rangePositionClasses(annotation.start_token_index, annotation.end_token_index, tokenIndex)

  const suggestion = props.suggestionForToken(sentence, tokenIndex)
  if (suggestion) return rangePositionClasses(suggestion.start_token_index, suggestion.end_token_index, tokenIndex)

  if (props.isTokenInDrag(sentence, tokenIndex)) return predicatePositionClasses(sentence, tokenIndex, props.isTokenInDrag)
  if (props.isTokenPending(sentence, tokenIndex)) return predicatePositionClasses(sentence, tokenIndex, props.isTokenPending)

  return {}
}

function rangePositionClasses(startTokenIndex: number, endTokenIndex: number, tokenIndex: number) {
  const isStart = tokenIndex === startTokenIndex
  const isEnd = tokenIndex === endTokenIndex
  return {
    'span-single': isStart && isEnd,
    'span-start': isStart && !isEnd,
    'span-middle': !isStart && !isEnd,
    'span-end': !isStart && isEnd,
  }
}

function predicatePositionClasses(
  sentence: SentenceDef,
  tokenIndex: number,
  predicate: (sentence: SentenceDef, tokenIndex: number) => boolean,
) {
  const previousToken = sentence.tokens[tokenIndex - 1]
  const nextToken = sentence.tokens[tokenIndex + 1]
  const hasPrevious = Boolean(previousToken && predicate(sentence, previousToken.token_index))
  const hasNext = Boolean(nextToken && predicate(sentence, nextToken.token_index))
  return {
    'span-single': !hasPrevious && !hasNext,
    'span-start': !hasPrevious && hasNext,
    'span-middle': hasPrevious && hasNext,
    'span-end': hasPrevious && !hasNext,
  }
}
</script>

<template>
  <section class="editor-panel" :aria-labelledby="documentMeta ? 'editor-title' : undefined" :aria-label="!documentMeta ? labels.emptyTitle : undefined">
    <div v-if="documentMeta" class="editor-header">
      <div>
        <p class="section-kicker">{{ labels.kicker }}</p>
        <h1 id="editor-title">{{ documentMeta?.filename ?? labels.emptyTitle }}</h1>
      </div>
      <div class="editor-tools">
        <label v-if="documents.length" class="document-switcher">
          <select :value="documentMeta?.id ?? ''" :disabled="isUploading" @change="handleDocumentChange">
            <option v-for="document in documents" :key="document.id" :value="document.id">
              {{ documentOptionText(document, labels) }}
            </option>
          </select>
        </label>
        <div class="reader-import-actions" :aria-label="labels.importActionsAria">
          <label class="import-action-button">
            {{ isUploading ? labels.importing : labels.replaceTxt }}
            <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="handleFileInput($event, 'replace')" />
          </label>
          <label class="import-action-button merge">
            {{ isUploading ? labels.importing : labels.mergeTxt }}
            <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="handleFileInput($event, 'merge')" />
          </label>
        </div>
        <div class="document-pill">
          <span>{{ currentSentence ? labels.sentence(currentSentence.index + 1) : labels.noDocument }}</span>
        </div>
      </div>
    </div>

    <p v-if="readerError" class="reader-error">{{ readerError }}</p>

    <article
      v-if="!documentMeta"
      class="empty-reader import-drop-zone"
      :class="{ 'drop-active': isDraggingTxt }"
      :aria-label="labels.emptyTitle"
      @dragenter="handleDragEnter"
      @dragover="handleDragOver"
      @dragleave="handleDragLeave"
      @drop="handleDrop($event, 'replace')"
    >
      <label class="primary-import-button">
        {{ isUploading ? labels.importingTxt : labels.importTxt }}
        <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="handleFileInput($event, 'replace')" />
      </label>
      <p>{{ labels.dropTxt }}</p>
    </article>

    <article
      v-else
      class="text-reader"
      :class="{ 'drop-active': isDraggingTxt }"
      :aria-label="labels.readerAria"
      :data-drop-hint="labels.dropToMerge"
      @dragenter="handleDragEnter"
      @dragover="handleDragOver"
      @dragleave="handleDragLeave"
      @drop="handleDrop($event, 'merge')"
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
          <strong>{{ sentenceStatusLabel(sentence, currentSentenceIndex, labels) }}</strong>
        </div>
        <p class="sentence-text">
          <template v-for="(token, tokenIndex) in sentence.tokens" :key="token.id">
            {{ tokenPrefix(sentence, tokenIndex) }}<button
              class="token"
              :class="[
                {
                  annotated: annotationForToken(sentence, token.token_index),
                  suggested: suggestionForToken(sentence, token.token_index),
                  selecting: isTokenInDrag(sentence, token.token_index),
                  pending: isTokenPending(sentence, token.token_index),
                },
                tokenSpanClasses(sentence, token.token_index),
              ]"
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
          <h2 id="candidate-title">{{ labels.spansTitle }}</h2>
          <span>{{ labels.selectedSuggested(activeAnnotations.length, activeSuggestions.length) }}</span>
        </div>
        <div class="candidate-actions">
          <label class="suggest-limit-control" :title="labels.limitTitle">
            <span>{{ labels.limit }}</span>
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
          <label class="suggest-limit-control" :title="labels.minTitle">
            <span>{{ labels.min }}</span>
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
            {{ isSuggesting ? labels.suggesting : labels.suggestCurrent }}
          </button>
          <button class="suggest-button secondary" :disabled="!documentMeta || isSuggesting" @click="emit('generate-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ labels.wholeDoc }}
          </button>
          <button class="batch-review-button accept" :disabled="!activeSuggestions.length || isSaving" @click="emit('accept-current-suggestions')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.acceptAll }}
          </button>
          <button class="batch-review-button accept" :disabled="!documentMeta || !hasReviewQueue || isSaving" @click="emit('auto-accept-document-suggestions')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.acceptMin }}
          </button>
          <button class="batch-review-button reject" :disabled="!activeSuggestions.length || isSaving" @click="emit('reject-current-suggestions')">
            <X :size="16" aria-hidden="true" />
            {{ labels.rejectAll }}
          </button>
          <button class="batch-review-button reject" :disabled="!documentMeta || !hasReviewQueue || isSaving" @click="emit('auto-reject-document-suggestions')">
            <X :size="16" aria-hidden="true" />
            {{ labels.rejectPending }}
          </button>
          <button class="review-button compact" :disabled="!hasReviewQueue" @click="emit('next-review')">
            {{ reviewQueueSummary }} · R
          </button>
        </div>
      </div>
      <div v-if="pendingSelection && pendingSelectionText" class="pending-card">
        <span>
          <strong>{{ pendingSelectionText }}</strong>
          <small>{{ labels.pendingHint }}</small>
        </span>
        <em>{{ labels.pending }}</em>
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
              <em v-if="annotation.source === 'accepted_suggestion'" class="source-badge">{{ labels.aiAccepted }}</em>
            </small>
          </span>
          <em>{{ labels.remove }}</em>
        </button>
      </div>
      <div v-if="activeSuggestions.length" class="suggestion-list" :aria-label="labels.suggestionsAria">
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
              <em class="suggestion-badge">{{ suggestionSourceLabel(suggestion.source, labels) }}</em>
              <em>{{ suggestion.tag_name }}</em>
              <em>{{ Math.round(suggestion.confidence * 100) }}%</em>
              <em>{{ suggestionRangeLabel(suggestion, labels) }}</em>
              <em v-if="suggestion.run_id">{{ suggestion.run_id.slice(0, 10) }}</em>
              <em v-if="suggestionIndex === 0" class="keyboard-target-badge">{{ labels.keyboardTarget }}</em>
            </small>
            <small v-if="suggestion.evidence_text" class="evidence-copy">
              <em>{{ labels.evidence }}</em>
              <strong>{{ suggestion.evidence_text }}</strong>
            </small>
            <small v-if="suggestion.context_before || suggestion.context_after" class="evidence-copy">
              <em>{{ labels.context }}</em>
              <strong>{{ suggestion.context_before }}[{{ suggestion.text }}]{{ suggestion.context_after }}</strong>
            </small>
            <small v-if="suggestionReviews[suggestion.id]" class="review-copy">
              <em>{{ labels.llmReview }}</em>
              {{ suggestionReviews[suggestion.id].recommendation }} ·
              {{ Math.round(suggestionReviews[suggestion.id].confidence * 100) }}% ·
              {{ suggestionReviews[suggestion.id].rationale }}
            </small>
          </span>
          <div class="suggestion-actions">
            <button
              type="button"
              :disabled="isSaving || reviewingSuggestionId === suggestion.id"
              :title="labels.reviewTitle"
              @click="emit('review-suggestion', suggestion)"
            >
              <Sparkles :size="15" aria-hidden="true" />
            </button>
            <button type="button" :disabled="isSaving" :title="labels.acceptTitle" @click="emit('accept-suggestion', suggestion)">
              <Check :size="15" aria-hidden="true" />
            </button>
            <button type="button" :disabled="isSaving" :title="labels.rejectTitle" @click="emit('reject-suggestion', suggestion)">
              <X :size="15" aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
      <p v-if="!activeAnnotations.length && !activeSuggestions.length" class="candidate-empty">
        {{ labels.emptyCandidate }}
      </p>
    </section>

    <section class="verification-card" :aria-label="labels.verificationAria">
      <div>
        <p class="section-kicker">{{ labels.verificationKicker }}</p>
        <h2>{{ labels.verificationTitle }}</h2>
      </div>
      <div class="verification-actions">
        <button class="accept-button" :disabled="!currentSentence || isSaving" @click="emit('complete')">
          {{ labels.complete }}
        </button>
        <button class="edit-button" :disabled="!currentSentence" @click="emit('previous')">{{ labels.previous }}</button>
        <button class="skip-button" :disabled="!currentSentence || isSaving" @click="emit('ignore')">{{ labels.ignore }}</button>
        <button class="reject-sentence-button" :disabled="!currentSentence || isSaving" @click="emit('reject')">{{ labels.rejectSentence }}</button>
        <button class="edit-button" :disabled="!currentSentence || isSaving" @click="emit('reopen')">{{ labels.reopen }}</button>
        <button class="undo-button" :disabled="!canUndoSpanAction || isSaving" :title="undoLabel" @click="emit('undo')">
          <Undo2 :size="16" aria-hidden="true" />
          {{ labels.undo }}
        </button>
        <button class="review-button" :disabled="!hasReviewQueue" @click="emit('next-review')">{{ reviewQueueSummary }}</button>
      </div>
    </section>
  </section>
</template>
