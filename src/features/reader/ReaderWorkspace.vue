<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle, Settings } from '@lucide/vue'
import { fetchRuntimeHealth } from '../../api/health'
import { useDocumentReader } from '../../composables/useDocumentReader'
import { LANGUAGE_KEY, UI_LABELS, type Locale } from '../../i18n'
import type { RuntimeHealth } from '../../types/domain'
import MetricsPanel from './MetricsPanel.vue'
import SentencePanel from './SentencePanel.vue'
import TagPalette from './TagPalette.vue'

const runtimeHealth = ref<RuntimeHealth | null>(null)
const healthUnavailable = ref(false)
const savedLocale = window.localStorage.getItem(LANGUAGE_KEY)
const locale = ref<Locale>(savedLocale === 'en' || savedLocale === 'zh' ? savedLocale : 'zh')
const labels = computed(() => UI_LABELS[locale.value])

const llmStatusLabel = computed(() => {
  if (healthUnavailable.value) return labels.value.topbar.llmUnavailable
  if (!runtimeHealth.value) return labels.value.topbar.checkingLlm
  if (!runtimeHealth.value.llm_configured) return labels.value.topbar.llmNotConfigured
  const model = runtimeHealth.value.llm_model ?? 'LLM'
  const host = runtimeHealth.value.llm_base_host ? ` @ ${runtimeHealth.value.llm_base_host}` : ''
  return `${model}${host}`
})

function setLocale(nextLocale: Locale) {
  locale.value = nextLocale
  window.localStorage.setItem(LANGUAGE_KEY, nextLocale)
}

onMounted(async () => {
  try {
    runtimeHealth.value = await fetchRuntimeHealth()
  } catch {
    healthUnavailable.value = true
  }
})

const {
  tags,
  documents,
  documentMeta,
  sentences,
  metrics,
  selectedTagId,
  currentSentenceIndex,
  suggestionLimit,
  suggestionMinConfidence,
  isUploading,
  isSaving,
  isSuggesting,
  isVerifyingRebuild,
  readerError,
  auditSummary,
  rebuildPreview,
  runHistory,
  reviewQueueDetails,
  reviewQueueTotal,
  reviewQueueOrder,
  suggestionReviews,
  reviewingSuggestionId,
  currentSentence,
  progressPercent,
  reviewedSummary,
  reviewSummary,
  reviewQueueSummary,
  activeAnnotations,
  activeSuggestions,
  canUndoSpanAction,
  undoLabel,
  hasReviewQueue,
  queueItems,
  pendingSelection,
  pendingSelectionText,
  handleImport,
  switchDocument,
  setCurrentSentence,
  jumpToNextReviewSentence,
  setReviewQueueOrder,
  completeCurrentSentence,
  reopenCurrentSentence,
  generateDocumentSuggestions,
  generateCurrentSentenceSuggestions,
  setSuggestionLimit,
  setSuggestionMinConfidence,
  setSentenceElement,
  onSentenceClick,
  onTokenPointerDown,
  onTokenPointerEnter,
  onTokenPointerUp,
  handleTagClick,
  addTag,
  renameTag,
  handleTagSchemaImport,
  deleteTag,
  removeAnnotation,
  undoLastSpanAction,
  acceptSuggestedSpan,
  rejectSuggestedSpan,
  acceptCurrentSentenceSuggestions,
  autoAcceptDocumentSuggestions,
  rejectCurrentSentenceSuggestions,
  autoRejectDocumentSuggestions,
  reviewSuggestedSpan,
  annotationForToken,
  suggestionForToken,
  isTokenInDrag,
  isTokenPending,
  tokenPrefix,
  tokenStyle,
  exportJsonl,
  exportProdigyJsonl,
  handleAnnotationImport,
  exportManifestJson,
  exportEventsJsonl,
  exportTagSchemaJson,
  exportRunProvenanceJson,
  verifyRebuildPreview,
} = useDocumentReader()

const localizedReviewSummary = computed(() => labels.value.tags.suggestionsWaiting(metrics.value.suggestion_count))
const localizedReviewQueueSummary = computed(() => {
  const reviewQueueItems = queueItems.value.filter((sentence) => !sentence.completed && sentence.suggestion_count > 0)
  if (!reviewQueueItems.length) return labels.value.reader.noReviewQueue
  const queueIndex = reviewQueueItems.findIndex((sentence) => sentence.index === currentSentenceIndex.value)
  return queueIndex >= 0
    ? labels.value.reader.reviewProgress(queueIndex + 1, reviewQueueItems.length)
    : labels.value.reader.pendingReviews(reviewQueueItems.length)
})
const localizedUndoLabel = computed(() => (canUndoSpanAction.value ? labels.value.reader.undoTitle : labels.value.reader.undoTitle))
</script>

<template>
  <main class="app-shell">
    <nav class="topbar" aria-label="Primary">
      <div class="brand-cluster">
        <div>
          <span class="brand-name">AnnoPilot</span>
          <small>{{ labels.topbar.tagline }}</small>
        </div>
      </div>

      <div class="runtime-statuses" :aria-label="labels.topbar.runtimeStatus">
        <div class="run-status">
          <CheckCircle :size="17" aria-hidden="true" />
          <span>SQLite + JSONL</span>
        </div>
        <div class="run-status llm-status" :class="{ muted: !runtimeHealth?.llm_configured }">
          <CheckCircle :size="17" aria-hidden="true" />
          <span>{{ llmStatusLabel }}</span>
        </div>
      </div>

      <div class="language-switcher" :aria-label="labels.topbar.language" role="group">
        <button type="button" :class="{ active: locale === 'zh' }" @click="setLocale('zh')">
          {{ labels.topbar.chinese }}
        </button>
        <button type="button" :class="{ active: locale === 'en' }" @click="setLocale('en')">
          {{ labels.topbar.english }}
        </button>
      </div>

      <button class="icon-button" :aria-label="labels.topbar.settings">
        <Settings :size="19" aria-hidden="true" />
      </button>
    </nav>

    <section class="workbench" :aria-label="labels.reader.aria">
      <TagPalette
        :labels="labels.tags"
        :tags="tags"
        :selected-tag-id="selectedTagId"
        :has-pending-selection="Boolean(pendingSelection)"
        :queue-items="queueItems"
        :current-sentence-index="currentSentenceIndex"
        :reviewed-summary="reviewedSummary"
        :review-summary="localizedReviewSummary"
        :review-queue-summary="localizedReviewQueueSummary"
        :is-saving="isSaving"
        @tag-click="handleTagClick"
        @tag-add="addTag"
        @tag-rename="renameTag"
        @tag-schema-import="handleTagSchemaImport"
        @tag-delete="deleteTag"
        @sentence-click="setCurrentSentence"
      />

      <SentencePanel
        :labels="labels.reader"
        :document-meta="documentMeta"
        :documents="documents"
        :current-sentence="currentSentence"
        :current-sentence-index="currentSentenceIndex"
        :sentences="sentences"
        :active-annotations="activeAnnotations"
        :active-suggestions="activeSuggestions"
        :can-undo-span-action="canUndoSpanAction"
        :undo-label="localizedUndoLabel"
        :pending-selection="pendingSelection"
        :pending-selection-text="pendingSelectionText"
        :has-review-queue="hasReviewQueue"
        :review-queue-summary="localizedReviewQueueSummary"
        :reader-error="readerError"
        :is-uploading="isUploading"
        :is-saving="isSaving"
        :is-suggesting="isSuggesting"
        :suggestion-limit="suggestionLimit"
        :suggestion-min-confidence="suggestionMinConfidence"
        :suggestion-reviews="suggestionReviews"
        :reviewing-suggestion-id="reviewingSuggestionId"
        :annotation-for-token="annotationForToken"
        :suggestion-for-token="suggestionForToken"
        :is-token-in-drag="isTokenInDrag"
        :is-token-pending="isTokenPending"
        :token-prefix="tokenPrefix"
        :token-style="tokenStyle"
        @import="handleImport"
        @document-change="switchDocument"
        @set-sentence-element="setSentenceElement"
        @sentence-click="onSentenceClick"
        @token-pointer-down="onTokenPointerDown"
        @token-pointer-enter="onTokenPointerEnter"
        @token-pointer-up="onTokenPointerUp"
        @delete-annotation="removeAnnotation"
        @undo="undoLastSpanAction"
        @generate-current-suggestions="generateCurrentSentenceSuggestions"
        @generate-suggestions="generateDocumentSuggestions"
        @accept-suggestion="acceptSuggestedSpan"
        @reject-suggestion="rejectSuggestedSpan"
        @accept-current-suggestions="acceptCurrentSentenceSuggestions"
        @auto-accept-document-suggestions="autoAcceptDocumentSuggestions"
        @reject-current-suggestions="rejectCurrentSentenceSuggestions"
        @auto-reject-document-suggestions="autoRejectDocumentSuggestions"
        @review-suggestion="reviewSuggestedSpan"
        @suggestion-limit-change="setSuggestionLimit"
        @suggestion-min-confidence-change="setSuggestionMinConfidence"
        @next-review="jumpToNextReviewSentence"
        @complete="completeCurrentSentence"
        @ignore="completeCurrentSentence('ignore')"
        @reject="completeCurrentSentence('reject')"
        @reopen="reopenCurrentSentence"
        @previous="setCurrentSentence(currentSentenceIndex - 1)"
        @next="setCurrentSentence(currentSentenceIndex + 1)"
      />

      <MetricsPanel
        :labels="labels.metrics"
        :document-meta="documentMeta"
        :metrics="metrics"
        :audit-summary="auditSummary"
        :rebuild-preview="rebuildPreview"
        :run-history="runHistory"
        :review-queue-details="reviewQueueDetails"
        :review-queue-total="reviewQueueTotal"
        :review-queue-order="reviewQueueOrder"
        :progress-percent="progressPercent"
        :reviewed-summary="reviewedSummary"
        :is-verifying-rebuild="isVerifyingRebuild"
        @export="exportJsonl"
        @export-prodigy="exportProdigyJsonl"
        @import-annotations="handleAnnotationImport"
        @review-sentence="setCurrentSentence"
        @review-order-change="setReviewQueueOrder"
        @export-manifest="exportManifestJson"
        @export-events="exportEventsJsonl"
        @export-tag-schema="exportTagSchemaJson"
        @export-run-provenance="exportRunProvenanceJson"
        @verify-rebuild="verifyRebuildPreview"
      />
    </section>
  </main>
</template>
