<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, Sparkles, Undo2, X } from '@lucide/vue'
import {
  buildAnnotationConflictPairs,
  conflictResolutionAnnotation,
  conflictResolutionAnnotationIds,
  type ConflictResolutionMode,
} from '../../composables/readerAnnotationConflicts'
import type { UiLabels } from '../../i18n'
import type {
  AnnotationDef,
  AssistanceErrorReason,
  AssistanceQueueItem,
  DocumentListItem,
  DocumentMeta,
  DragSelection,
  ReviewQueueInsight,
  SamplePreset,
  SentenceDef,
  SuggestionDef,
  SuggestionReview,
  TxtImportMode,
} from '../../types/domain'
import { truncateFilename } from '../../utils/display'
import SuggestionRow from './SuggestionRow.vue'

const SWIPE_MIN_DISTANCE = 56
const SWIPE_AXIS_RATIO = 1.4
const DOCUMENT_TITLE_FILENAME_LIMIT = 34
const DOCUMENT_OPTION_FILENAME_LIMIT = 28

const props = defineProps<{
  labels: UiLabels['reader']
  samplePresets: SamplePreset[]
  documentMeta: DocumentMeta | null
  documents: DocumentListItem[]
  currentSentence: SentenceDef | null
  currentSentenceIndex: number
  sentences: SentenceDef[]
  activeAnnotations: AnnotationDef[]
  activeAssistanceAnnotations: AnnotationDef[]
  activeSuggestions: SuggestionDef[]
  assistanceDraft: AssistanceQueueItem | null
  assistanceDraftModified: boolean
  assistanceErrorReasons: AssistanceErrorReason[]
  isAssistanceDeciding: boolean
  canUndoSpanAction: boolean
  undoLabel: string
  pendingSelection: DragSelection | null
  pendingSelectionText: string
  hasReviewQueue: boolean
  reviewQueueSummary: string
  reviewQueueInsight: ReviewQueueInsight | null
  reviewedSuggestionCount: number
  readerError: string
  isUploading: boolean
  isSaving: boolean
  isSuggesting: boolean
  suggestionLimit: number
  suggestionMinConfidence: number
  engagementCandidateCount: number
  engagementTemperature: number
  lastEngagementRun: { runId: string; candidateCount: number; routeCounts: Record<string, number>; verifierFailures: number } | null
  suggestionReviews: Record<string, SuggestionReview>
  reviewingSuggestionId: string
  activeSuggestionTargetId: string
  activeSuggestionPosition: number
  focusedConflictAnnotationIds: string[]
  annotationForToken: (sentence: SentenceDef, tokenIndex: number) => AnnotationDef | undefined
  suggestionForToken: (sentence: SentenceDef, tokenIndex: number) => SuggestionDef | undefined
  isTokenInDrag: (sentence: SentenceDef, tokenIndex: number) => boolean
  isTokenPending: (sentence: SentenceDef, tokenIndex: number) => boolean
  tokenPrefix: (sentence: SentenceDef, tokenIndex: number) => string
  tokenStyle: (sentence: SentenceDef, tokenIndex: number) => Record<string, string>
}>()

const renderedSentences = computed(() =>
  props.sentences
    .filter((sentence) => Math.abs(sentence.index - props.currentSentenceIndex) <= 1)
    .sort((left, right) => left.index - right.index),
)

const annotationConflictPairs = computed(() => buildAnnotationConflictPairs(props.activeAnnotations))

const conflictedAnnotationIds = computed(() => new Set(annotationConflictPairs.value.flatMap((pair) => [pair.left.id, pair.right.id])))
const focusedConflictAnnotationIdSet = computed(() => new Set(props.focusedConflictAnnotationIds))

const narrowerConflictAnnotationIds = computed(() => uniqueConflictResolutionIds('narrower'))
const widerConflictAnnotationIds = computed(() => uniqueConflictResolutionIds('wider'))

const emit = defineEmits<{
  import: [file: File, mode: TxtImportMode]
  'load-sample-preset': [presetId: string]
  'document-change': [documentId: string]
  'set-sentence-element': [sentenceId: string, element: unknown]
  'sentence-click': [sentenceIndex: number]
  'token-pointer-down': [sentence: SentenceDef, tokenIndex: number, event: PointerEvent]
  'token-pointer-enter': [sentence: SentenceDef, tokenIndex: number]
  'token-pointer-up': [sentence: SentenceDef, tokenIndex: number]
  'annotation-hover': [annotationId: string | null]
  'select-current-sentence': []
  'mark-current-monogloss': []
  'delete-annotation': [annotationId: string]
  'delete-annotations': [annotationIds: string[]]
  undo: []
  'generate-current-suggestions': []
  'generate-suggestions': []
  'generate-engagement-candidates': []
  'auto-annotate-document': []
  'accept-suggestion': [suggestion: SuggestionDef]
  'reject-suggestion': [suggestion: SuggestionDef]
  'suggestion-target': [suggestion: SuggestionDef]
  'accept-current-suggestions': []
  'auto-accept-document-suggestions': []
  'reject-current-suggestions': []
  'auto-reject-document-suggestions': []
  'review-suggestion': [suggestion: SuggestionDef]
  'review-current-suggestions': []
  'apply-current-reviews': []
  'apply-document-reviews': []
  'suggestion-limit-change': [limit: number]
  'suggestion-min-confidence-change': [minConfidence: number]
  'engagement-candidate-count-change': [count: number]
  'engagement-temperature-change': [temperature: number]
  'next-review': []
  complete: []
  'assistance-skip': []
  'assistance-error-toggle': [reason: AssistanceErrorReason]
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

function handleEngagementCandidateCountInput(event: Event) {
  emit('engagement-candidate-count-change', Number((event.target as HTMLInputElement).value))
}

function handleEngagementTemperatureInput(event: Event) {
  emit('engagement-temperature-change', Number((event.target as HTMLInputElement).value))
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
  const progress = Math.min(Math.max(document.progress * 100, 0), 100)
  const cursor = typeof document.current_sentence_index === 'number' ? labels.cursor(document.current_sentence_index + 1) : ''
  return labels.documentOption(
    truncateFilename(document.filename, DOCUMENT_OPTION_FILENAME_LIMIT),
    cursor,
    progress,
    document.annotation_count,
    document.suggestion_count,
  )
}

function documentTitleText(document: DocumentMeta | null, fallback: string) {
  return document ? truncateFilename(document.filename, DOCUMENT_TITLE_FILENAME_LIMIT) : fallback
}

function samplePresetButtonText(preset: SamplePreset, labels: UiLabels['reader']) {
  return labels.loadSamplePreset(truncateFilename(preset.title, 34))
}

function annotationRangeText(annotation: AnnotationDef, labels: UiLabels['reader']) {
  const tokenRange = annotation.start_token_index === annotation.end_token_index
    ? `${labels.tokenRange} ${annotation.start_token_index}`
    : `${labels.tokenRange} ${annotation.start_token_index}-${annotation.end_token_index}`
  return `${tokenRange} · ${labels.charRange} ${annotation.start_char}-${annotation.end_char}`
}

function shortSpanText(text: string) {
  return text.length > 22 ? `${text.slice(0, 10)}...${text.slice(-8)}` : text
}

function isAnnotationConflicted(annotationId: string) {
  return conflictedAnnotationIds.value.has(annotationId)
}

function isAnnotationConflictFocused(annotationId: string) {
  return focusedConflictAnnotationIdSet.value.has(annotationId)
}

function isConflictPairFocused(pair: { left: AnnotationDef; right: AnnotationDef }) {
  return isAnnotationConflictFocused(pair.left.id) || isAnnotationConflictFocused(pair.right.id)
}

function uniqueConflictResolutionIds(mode: ConflictResolutionMode) {
  return conflictResolutionAnnotationIds(annotationConflictPairs.value, mode)
}

function deleteConflictResolution(mode: ConflictResolutionMode) {
  const annotationIds = mode === 'narrower' ? narrowerConflictAnnotationIds.value : widerConflictAnnotationIds.value
  if (annotationIds.length) emit('delete-annotations', annotationIds)
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

function sentenceStatusLabel(sentence: SentenceDef, currentSentenceIndex: number, labels: UiLabels['reader']) {
  if (sentence.answer === 'reject') return labels.statusRejected
  if (sentence.answer === 'ignore') return labels.statusIgnored
  if (sentence.completed) return labels.statusCompleted
  if (props.assistanceDraft?.sentence_id === sentence.id) return labels.assistanceDraft
  if (sentence.suggestions.length) return labels.statusReview
  if (sentence.index === currentSentenceIndex) return labels.statusActive
  return labels.statusWaiting
}

const assistanceReasonKeys: AssistanceErrorReason[] = [
  'missed_span',
  'extra_span',
  'wrong_label',
  'boundary_too_wide',
  'boundary_too_narrow',
  'other',
]

function tokenSpanClasses(sentence: SentenceDef, tokenIndex: number) {
  if (props.isTokenInDrag(sentence, tokenIndex)) return predicatePositionClasses(sentence, tokenIndex, props.isTokenInDrag)
  if (props.isTokenPending(sentence, tokenIndex)) return predicatePositionClasses(sentence, tokenIndex, props.isTokenPending)

  const focusedConflictAnnotation = focusedConflictAnnotationForToken(sentence, tokenIndex)
  if (focusedConflictAnnotation) return rangePositionClasses(focusedConflictAnnotation.start_token_index, focusedConflictAnnotation.end_token_index, tokenIndex)

  const annotation = props.annotationForToken(sentence, tokenIndex)
  if (annotation) return rangePositionClasses(annotation.start_token_index, annotation.end_token_index, tokenIndex)

  const suggestion = props.suggestionForToken(sentence, tokenIndex)
  if (suggestion) return rangePositionClasses(suggestion.start_token_index, suggestion.end_token_index, tokenIndex)

  return {}
}

function annotationIdForToken(sentence: SentenceDef, tokenIndex: number) {
  const annotation = props.annotationForToken(sentence, tokenIndex)
  if (annotation) return annotation.id
  if (sentence.index !== props.currentSentenceIndex) return null
  return props.activeAssistanceAnnotations.find((item) =>
    item.start_token_index <= tokenIndex && item.end_token_index >= tokenIndex,
  )?.id ?? null
}

function handleTokenPointerEnter(sentence: SentenceDef, tokenIndex: number) {
  emit('annotation-hover', annotationIdForToken(sentence, tokenIndex))
  emit('token-pointer-enter', sentence, tokenIndex)
}

function focusedConflictAnnotationForToken(sentence: SentenceDef, tokenIndex: number) {
  if (sentence.index !== props.currentSentenceIndex) return undefined
  return props.activeAnnotations.find((annotation) =>
    focusedConflictAnnotationIdSet.value.has(annotation.id) &&
      annotation.start_token_index <= tokenIndex &&
      annotation.end_token_index >= tokenIndex,
  )
}

function isConflictTargetForToken(sentence: SentenceDef, tokenIndex: number) {
  return Boolean(focusedConflictAnnotationForToken(sentence, tokenIndex))
}

function isSuggestionTargetForToken(sentence: SentenceDef, tokenIndex: number) {
  const suggestion = props.activeSuggestions.find((item) => item.id === props.activeSuggestionTargetId)
  return Boolean(
    suggestion &&
      suggestion.sentence_id === sentence.id &&
      suggestion.start_token_index <= tokenIndex &&
      suggestion.end_token_index >= tokenIndex,
  )
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
  <section class="editor-panel" data-testid="reader-panel" :aria-labelledby="documentMeta ? 'editor-title' : undefined" :aria-label="!documentMeta ? labels.emptyTitle : undefined">
    <div v-if="documentMeta" class="editor-header">
      <div>
        <p class="section-kicker">{{ labels.kicker }}</p>
        <h1 id="editor-title" :title="documentMeta?.filename">{{ documentTitleText(documentMeta, labels.emptyTitle) }}</h1>
      </div>
      <div class="editor-tools">
        <label v-if="documents.length" class="document-switcher">
          <select :value="documentMeta?.id ?? ''" :disabled="isUploading" @change="handleDocumentChange">
            <option v-for="document in documents" :key="document.id" :value="document.id" :title="document.filename">
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
        <div v-if="annotationConflictPairs.length" class="document-pill conflict-pill" :title="labels.annotationConflictHint">
          <span>{{ labels.annotationConflictTitle }} · {{ labels.annotationConflictCount(annotationConflictPairs.length) }}</span>
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
      <div v-if="samplePresets.length" class="sample-preset-list" :aria-label="labels.samplePresetsAria">
        <span>{{ labels.samplePresetsTitle }}</span>
        <button
          v-for="preset in samplePresets"
          :key="preset.id"
          type="button"
          class="sample-preset-button"
          :data-testid="`sample-preset-${preset.id}`"
          :disabled="isUploading || isSuggesting"
          :title="preset.description"
          @click="emit('load-sample-preset', preset.id)"
        >
          {{ isUploading || isSuggesting ? labels.loadingSample : samplePresetButtonText(preset, labels) }}
        </button>
      </div>
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
      @pointerleave="clearReaderSwipe(); emit('annotation-hover', null)"
    >
      <section
        v-for="sentence in renderedSentences"
        :key="sentence.id"
        :ref="(element) => emit('set-sentence-element', sentence.id, element)"
        class="sentence-card"
        :data-testid="`sentence-${sentence.index}`"
        :class="{
          active: sentence.index === currentSentenceIndex,
          dimmed: sentence.index !== currentSentenceIndex,
          completed: sentence.completed,
          rejected: sentence.answer === 'reject',
          ignored: sentence.answer === 'ignore',
          'needs-review': !sentence.completed && (sentence.suggestions.length > 0 || assistanceDraft?.sentence_id === sentence.id),
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
              :data-testid="`token-${sentence.index}-${token.token_index}`"
              :class="[
                {
                  annotated: annotationForToken(sentence, token.token_index),
                  suggested: suggestionForToken(sentence, token.token_index),
                  targeted: isSuggestionTargetForToken(sentence, token.token_index),
                  'conflict-targeted': isConflictTargetForToken(sentence, token.token_index),
                  selecting: isTokenInDrag(sentence, token.token_index),
                  pending: isTokenPending(sentence, token.token_index),
                },
                tokenSpanClasses(sentence, token.token_index),
              ]"
              :style="tokenStyle(sentence, token.token_index)"
              @pointerdown="emit('token-pointer-down', sentence, token.token_index, $event)"
              @pointerenter="handleTokenPointerEnter(sentence, token.token_index)"
              @pointerleave="emit('annotation-hover', null)"
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
          <span>{{ labels.selectedSuggested(activeAnnotations.length + activeAssistanceAnnotations.length, activeSuggestions.length) }}</span>
          <em v-if="assistanceDraft" class="assistance-draft-state" :class="{ modified: assistanceDraftModified }">
            {{ assistanceDraftModified ? labels.assistanceModified : labels.assistanceUnchanged }}
          </em>
        </div>
        <div v-if="annotationConflictPairs.length" class="annotation-conflict-inline" :title="labels.annotationConflictHint">
          <strong>{{ labels.annotationConflictTitle }}</strong>
          <span>{{ labels.annotationConflictCount(annotationConflictPairs.length) }}</span>
        </div>
        <details v-if="!assistanceDraft" class="advanced-suggestion-tools">
          <summary>
            <Sparkles :size="15" aria-hidden="true" />
            {{ labels.advancedDiagnostics }}
          </summary>
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
          <label class="suggest-limit-control engagement-control" :title="labels.engagementCandidateCountTitle">
            <span>K</span>
            <input
              type="number"
              min="3"
              max="7"
              step="1"
              :value="engagementCandidateCount"
              :disabled="isSuggesting"
              @input="handleEngagementCandidateCountInput"
            />
          </label>
          <label class="suggest-limit-control engagement-control" :title="labels.engagementTemperatureTitle">
            <span>T</span>
            <input
              type="number"
              min="0"
              max="1.5"
              step="0.1"
              :value="engagementTemperature"
              :disabled="isSuggesting"
              @input="handleEngagementTemperatureInput"
            />
          </label>
          <button class="suggest-button" :disabled="!currentSentence || isSuggesting" @click="emit('generate-current-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ isSuggesting ? labels.suggesting : labels.suggestCurrent }}
          </button>
          <button class="suggest-button secondary" :disabled="!documentMeta || isSuggesting" @click="emit('generate-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ labels.wholeDoc }}
          </button>
          <button class="suggest-button engagement-button" :disabled="!currentSentence || isSuggesting" @click="emit('generate-engagement-candidates')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ isSuggesting ? labels.engagementSuggesting : labels.generateEngagementCandidates }}
          </button>
          <button class="suggest-button secondary" :disabled="!currentSentence || isSaving" @click="emit('select-current-sentence')">
            {{ labels.selectSentence }}
          </button>
          <button class="suggest-button secondary" :disabled="!currentSentence || isSaving" @click="emit('mark-current-monogloss')">
            {{ labels.markMonogloss }}
          </button>
          <button class="batch-review-button accept" :disabled="!documentMeta || isSuggesting || isSaving" @click="emit('auto-annotate-document')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.autoAnnotate }}
          </button>
          <button class="batch-review-button accept" :disabled="!activeSuggestions.length || isSaving" @click="emit('accept-current-suggestions')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.acceptAll }}
          </button>
          <button class="batch-review-button" :disabled="!activeSuggestions.length || isSaving || !!reviewingSuggestionId" @click="emit('review-current-suggestions')">
            <Sparkles :size="16" aria-hidden="true" />
            {{ labels.reviewCurrent }}
          </button>
          <button class="batch-review-button accept" :disabled="!activeSuggestions.length || isSaving" @click="emit('apply-current-reviews')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.applyReview }}
          </button>
          <button class="batch-review-button accept" :disabled="!documentMeta || !reviewedSuggestionCount || isSaving" @click="emit('apply-document-reviews')">
            <Check :size="16" aria-hidden="true" />
            {{ labels.applyAllReviews }}
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
        </details>
      </div>
      <div v-if="assistanceDraft" class="assistance-draft-banner">
        <span>
          <strong>{{ labels.assistanceDraft }}</strong>
          <small>{{ labels.assistanceDraftHint }}</small>
        </span>
        <em>{{ assistanceDraft.model || 'LLM' }}</em>
      </div>
      <div v-if="pendingSelection && pendingSelectionText" class="pending-card">
        <span>
          <strong>{{ pendingSelectionText }}</strong>
          <small>{{ labels.pendingHint }}</small>
        </span>
        <em>{{ labels.pending }}</em>
      </div>
      <div v-if="reviewQueueInsight && !assistanceDraft" class="review-insight-card">
        <span>
          <strong>{{ reviewQueueInsight.headline }}</strong>
          <small>{{ reviewQueueInsight.detail }}</small>
          <small v-if="reviewQueueInsight.actionHint">{{ reviewQueueInsight.actionHint }}</small>
        </span>
        <em v-for="reason in reviewQueueInsight.reasons" :key="reason">{{ reason }}</em>
      </div>
      <div v-if="annotationConflictPairs.length" class="annotation-conflict-card" :aria-label="labels.annotationConflictTitle">
        <div class="annotation-conflict-summary">
          <span>
            <strong>{{ labels.annotationConflictTitle }}</strong>
            <small>{{ labels.annotationConflictHint }}</small>
          </span>
          <div class="annotation-conflict-actions compact">
            <button type="button" :disabled="isSaving" @click="deleteConflictResolution('narrower')">
              {{ labels.deleteAllNarrowerConflicts(narrowerConflictAnnotationIds.length) }}
            </button>
            <button type="button" :disabled="isSaving" @click="deleteConflictResolution('wider')">
              {{ labels.deleteAllWiderConflicts(widerConflictAnnotationIds.length) }}
            </button>
          </div>
        </div>
        <div class="annotation-conflict-list">
          <div
            v-for="pair in annotationConflictPairs"
            :key="pair.id"
            class="annotation-conflict-pair"
            :class="{ focused: isConflictPairFocused(pair) }"
          >
            <span>
              <strong>{{ labels.annotationConflictPair(shortSpanText(pair.left.text), shortSpanText(pair.right.text)) }}</strong>
              <small>{{ annotationRangeText(pair.left, labels) }} · {{ annotationRangeText(pair.right, labels) }}</small>
            </span>
            <div class="annotation-conflict-actions">
              <button type="button" :disabled="isSaving" @click="emit('delete-annotation', conflictResolutionAnnotation(pair, 'narrower').id)">
                {{ labels.deleteNarrowerConflictSpan(shortSpanText(conflictResolutionAnnotation(pair, 'narrower').text)) }}
              </button>
              <button type="button" :disabled="isSaving" @click="emit('delete-annotation', conflictResolutionAnnotation(pair, 'wider').id)">
                {{ labels.deleteWiderConflictSpan(shortSpanText(conflictResolutionAnnotation(pair, 'wider').text)) }}
              </button>
              <button type="button" :disabled="isSaving" @click="emit('delete-annotation', pair.left.id)">
                {{ labels.deleteConflictSpan(shortSpanText(pair.left.text)) }}
              </button>
              <button type="button" :disabled="isSaving" @click="emit('delete-annotation', pair.right.id)">
                {{ labels.deleteConflictSpan(shortSpanText(pair.right.text)) }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="activeAnnotations.length" class="candidate-list">
        <button
          v-for="annotation in activeAnnotations"
          :key="annotation.id"
          class="candidate-row"
          :class="{ conflict: isAnnotationConflicted(annotation.id), focused: isAnnotationConflictFocused(annotation.id) }"
          :style="{ '--token-color': annotation.tag_color }"
          @click="emit('delete-annotation', annotation.id)"
        >
          <span>
            <strong>{{ annotation.text }}</strong>
            <small>
              {{ annotation.tag_name }}
              <em v-if="annotation.source === 'accepted_suggestion'" class="source-badge">{{ labels.aiAccepted }}</em>
              <em v-if="isAnnotationConflicted(annotation.id)" class="source-badge conflict-badge">{{ labels.conflictBadge }}</em>
            </small>
          </span>
          <em>{{ labels.remove }}</em>
        </button>
      </div>
      <div v-if="activeAssistanceAnnotations.length" class="candidate-list assistance-draft-list">
        <button
          v-for="annotation in activeAssistanceAnnotations"
          :key="annotation.id"
          class="candidate-row assistance-draft-row"
          :style="{ '--token-color': annotation.tag_color }"
          @click="emit('delete-annotation', annotation.id)"
        >
          <span>
            <strong>{{ annotation.text }}</strong>
            <small>{{ annotation.tag_name }} · {{ labels.assistanceDraft }}</small>
          </span>
          <em>{{ labels.remove }}</em>
        </button>
      </div>
      <div v-if="assistanceDraft && assistanceDraftModified" class="assistance-error-reasons">
        <span>{{ labels.assistanceErrors }}</span>
        <div>
          <button
            v-for="reason in assistanceReasonKeys"
            :key="reason"
            type="button"
            :class="{ active: assistanceErrorReasons.includes(reason) }"
            @click="emit('assistance-error-toggle', reason)"
          >
            {{ labels.assistanceErrorReasons[reason] }}
          </button>
        </div>
      </div>
      <div v-if="activeSuggestions.length" class="suggestion-list" :aria-label="labels.suggestionsAria">
        <SuggestionRow
          v-for="suggestion in activeSuggestions"
          :key="suggestion.id"
          :labels="labels"
          :suggestion="suggestion"
          :review="suggestionReviews[suggestion.id]"
          :is-saving="isSaving"
          :is-reviewing="!!reviewingSuggestionId"
          :is-keyboard-target="suggestion.id === activeSuggestionTargetId"
          :active-position="activeSuggestionPosition"
          :total-suggestions="activeSuggestions.length"
          @target="emit('suggestion-target', $event)"
          @review="emit('review-suggestion', $event)"
          @accept="emit('accept-suggestion', $event)"
          @reject="emit('reject-suggestion', $event)"
        />
      </div>
      <div v-if="lastEngagementRun" class="engagement-run-summary">
        <strong>{{ labels.engagementRunSummary(lastEngagementRun.candidateCount) }}</strong>
        <span>{{ labels.engagementRoutes(lastEngagementRun.routeCounts) }}</span>
        <em v-if="lastEngagementRun.verifierFailures">{{ labels.engagementVerifierFailures(lastEngagementRun.verifierFailures) }}</em>
        <em v-else>{{ labels.engagementReviewRequired }}</em>
      </div>
      <p v-if="!activeAnnotations.length && !activeAssistanceAnnotations.length && !activeSuggestions.length" class="candidate-empty">
        {{ labels.emptyCandidate }}
      </p>
    </section>

    <section class="verification-card" :aria-label="labels.verificationAria">
      <div>
        <p class="section-kicker">{{ labels.verificationKicker }}</p>
        <h2>{{ labels.verificationTitle }}</h2>
      </div>
      <div class="verification-actions">
        <template v-if="assistanceDraft">
          <button class="skip-button" :disabled="isSaving || isAssistanceDeciding" @click="emit('assistance-skip')">
            {{ labels.assistanceSkip }}
          </button>
          <button class="accept-button" :disabled="isSaving || isAssistanceDeciding" @click="emit('complete')">
            {{ assistanceDraftModified ? labels.assistanceConfirmModified : labels.assistanceConfirm }}
          </button>
        </template>
        <template v-else>
          <button data-testid="complete-sentence" class="accept-button" :disabled="!currentSentence || isSaving" @click="emit('complete')">
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
        </template>
      </div>
    </section>
  </section>
</template>
