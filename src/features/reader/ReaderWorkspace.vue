<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle, Settings } from '@lucide/vue'
import { fetchRuntimeHealth } from '../../api/health'
import { useDocumentReader } from '../../composables/useDocumentReader'
import type { RuntimeHealth } from '../../types/domain'
import MetricsPanel from './MetricsPanel.vue'
import SentencePanel from './SentencePanel.vue'
import TagPalette from './TagPalette.vue'

const runtimeHealth = ref<RuntimeHealth | null>(null)
const healthUnavailable = ref(false)

const llmStatusLabel = computed(() => {
  if (healthUnavailable.value) return 'LLM status unavailable'
  if (!runtimeHealth.value) return 'Checking LLM'
  if (!runtimeHealth.value.llm_configured) return 'LLM not configured'
  const model = runtimeHealth.value.llm_model ?? 'LLM'
  const host = runtimeHealth.value.llm_base_host ? ` @ ${runtimeHealth.value.llm_base_host}` : ''
  return `${model}${host}`
})

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
</script>

<template>
  <main class="app-shell">
    <nav class="topbar" aria-label="Primary">
      <div class="brand-cluster">
        <div class="brand-mark" aria-hidden="true">A</div>
        <div>
          <span class="brand-name">AnnoPilot</span>
          <small>Persistent TXT annotation reader</small>
        </div>
      </div>

      <div class="runtime-statuses" aria-label="Runtime status">
        <div class="run-status">
          <CheckCircle :size="17" aria-hidden="true" />
          <span>SQLite + JSONL</span>
        </div>
        <div class="run-status llm-status" :class="{ muted: !runtimeHealth?.llm_configured }">
          <CheckCircle :size="17" aria-hidden="true" />
          <span>{{ llmStatusLabel }}</span>
        </div>
      </div>

      <button class="icon-button" aria-label="Open settings">
        <Settings :size="19" aria-hidden="true" />
      </button>
    </nav>

    <section class="workbench" aria-label="Annotation workspace">
      <TagPalette
        :tags="tags"
        :selected-tag-id="selectedTagId"
        :has-pending-selection="Boolean(pendingSelection)"
        :queue-items="queueItems"
        :current-sentence-index="currentSentenceIndex"
        :reviewed-summary="reviewedSummary"
        :review-summary="reviewSummary"
        :review-queue-summary="reviewQueueSummary"
        :is-saving="isSaving"
        @tag-click="handleTagClick"
        @tag-add="addTag"
        @tag-rename="renameTag"
        @tag-schema-import="handleTagSchemaImport"
        @tag-delete="deleteTag"
        @sentence-click="setCurrentSentence"
      />

      <SentencePanel
        :document-meta="documentMeta"
        :documents="documents"
        :current-sentence="currentSentence"
        :current-sentence-index="currentSentenceIndex"
        :sentences="sentences"
        :active-annotations="activeAnnotations"
        :active-suggestions="activeSuggestions"
        :can-undo-span-action="canUndoSpanAction"
        :undo-label="undoLabel"
        :pending-selection="pendingSelection"
        :pending-selection-text="pendingSelectionText"
        :has-review-queue="hasReviewQueue"
        :review-queue-summary="reviewQueueSummary"
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
