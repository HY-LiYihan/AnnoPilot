<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { CheckCircle, Settings } from '@lucide/vue'
import { fetchRuntimeHealth } from '../../api/health'
import { fetchLlmSettings, updateLlmSettings } from '../../api/settings'
import { useDocumentReader } from '../../composables/useDocumentReader'
import { LANGUAGE_KEY, UI_LABELS, type Locale } from '../../i18n'
import type { LlmModelOption, LlmSettingsState, ReviewQueueInsight, ReviewQueueItem, RuntimeHealth } from '../../types/domain'
import MetricsPanel from './MetricsPanel.vue'
import SentencePanel from './SentencePanel.vue'
import TagPalette from './TagPalette.vue'

const runtimeHealth = ref<RuntimeHealth | null>(null)
const llmSettings = ref<LlmSettingsState | null>(null)
const healthUnavailable = ref(false)
const isSettingsOpen = ref(false)
const isSavingLlmSettings = ref(false)
const settingsError = ref('')
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

const selectedModelOption = computed(() => {
  const selectedId = llmSettings.value?.selected_model_option_id
  return llmSettings.value?.model_options.find((option) => option.id === selectedId) ?? null
})

const modelButtonLabel = computed(() => selectedModelOption.value?.model ?? llmSettings.value?.model ?? runtimeHealth.value?.llm_model ?? 'LLM')

const settingsHint = computed(() => {
  if (llmSettings.value?.configured && llmSettings.value.base_host) return labels.value.topbar.modelConfigured(llmSettings.value.base_host)
  return labels.value.topbar.modelNotConfigured
})

function setLocale(nextLocale: Locale) {
  locale.value = nextLocale
  window.localStorage.setItem(LANGUAGE_KEY, nextLocale)
}

async function refreshRuntimeStatus() {
  try {
    runtimeHealth.value = await fetchRuntimeHealth()
    healthUnavailable.value = false
  } catch {
    healthUnavailable.value = true
  }

  try {
    llmSettings.value = await fetchLlmSettings()
    settingsError.value = ''
  } catch (error) {
    settingsError.value = error instanceof Error ? error.message : labels.value.topbar.llmUnavailable
  }
}

function qualityLabel(tier: string) {
  const quality = labels.value.topbar.quality as Record<string, string>
  return quality[tier] ?? tier
}

function qualityHint(tier: string) {
  const hint = labels.value.topbar.qualityHint as Record<string, string>
  return hint[tier] ?? ''
}

function modelOptionLabel(option: LlmModelOption) {
  return `${option.family} · ${qualityLabel(option.tier)}`
}

async function selectLlmOption(optionId: string) {
  if (isSavingLlmSettings.value || optionId === llmSettings.value?.selected_model_option_id) return
  isSavingLlmSettings.value = true
  settingsError.value = ''
  try {
    llmSettings.value = await updateLlmSettings(optionId)
    runtimeHealth.value = await fetchRuntimeHealth()
    healthUnavailable.value = false
  } catch (error) {
    settingsError.value = error instanceof Error ? error.message : labels.value.topbar.modelSaveFailed
  } finally {
    isSavingLlmSettings.value = false
  }
}

onMounted(async () => {
  await refreshRuntimeStatus()
})

const {
  tags,
  samplePresets,
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
  isResetting,
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
  activeSuggestionTargetId,
  activeSuggestionPosition,
  lastAnnotationImport,
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
  loadBuiltinSamplePreset,
  switchDocument,
  setCurrentSentence,
  jumpToNextReviewSentence,
  setReviewQueueOrder,
  markCurrentSentenceMonogloss,
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
  selectCurrentSentenceSpan,
  handleTagClick,
  addTag,
  renameTag,
  handleTagSchemaImport,
  deleteTag,
  removeAnnotation,
  undoLastSpanAction,
  acceptSuggestedSpan,
  rejectSuggestedSpan,
  setActiveSuggestionTarget,
  acceptCurrentSentenceSuggestions,
  autoAnnotateDocument,
  autoAcceptDocumentSuggestions,
  rejectCurrentSentenceSuggestions,
  autoRejectDocumentSuggestions,
  reviewSuggestedSpan,
  reviewCurrentSentenceSuggestions,
  applyCurrentSentenceSuggestionReviews,
  applyDocumentSuggestionReviewsFromLlm,
  annotationForToken,
  suggestionForToken,
  isTokenInDrag,
  isTokenPending,
  tokenPrefix,
  tokenStyle,
  exportJsonl,
  exportProdigyJsonl,
  exportProdigyBundleZip,
  exportProdigyLabelsJson,
  exportProdigySpansJsonl,
  handleAnnotationImport,
  exportManifestJson,
  exportEventsJsonl,
  exportTagSchemaJson,
  exportRunProvenanceJson,
  exportGoldsmithReviewQueueJsonl,
  exportGoldsmithHumanChoicesJsonl,
  exportGoldsmithHardExamplesJsonl,
  exportGoldsmithBoundaryFeedbackJsonl,
  exportGoldsmithConsistencyScoresJsonl,
  exportGoldsmithCandidateRunsJsonl,
  exportGoldsmithRiskReasonsJsonl,
  exportGoldsmithReviewTasksJsonl,
  verifyRebuildPreview,
  resetProjectData,
} = useDocumentReader()

const localizedReviewSummary = computed(() => labels.value.tags.suggestionsWaiting(metrics.value.suggestion_count))
const localizedReviewQueueSummary = computed(() => {
  const reviewQueueItems = queueItems.value.filter((sentence) => !sentence.completed && sentence.suggestion_count > 0)
  const orderedItems = reviewQueueOrder.value === 'position' || !reviewQueueDetails.value.length
    ? reviewQueueItems
    : reviewQueueDetails.value
  const total = reviewQueueOrder.value === 'position'
    ? reviewQueueItems.length
    : reviewQueueTotal.value || orderedItems.length || reviewQueueItems.length
  if (!total) return labels.value.reader.noReviewQueue
  const queueIndex = orderedItems.findIndex((sentence) => sentence.index === currentSentenceIndex.value)
  const prefix = reviewQueueOrderLabel()
  const summary = queueIndex >= 0
    ? labels.value.reader.reviewProgress(queueIndex + 1, total)
    : labels.value.reader.pendingReviews(total)
  return prefix ? `${prefix} · ${summary}` : summary
})

const currentReviewQueueInsight = computed<ReviewQueueInsight | null>(() => {
  if (reviewQueueOrder.value === 'position') return null
  const item = reviewQueueDetails.value.find((queueItem) => queueItem.index === currentSentenceIndex.value)
  if (!item) return null
  const orderLabel = reviewQueueOrderLabel()
  const headline = `${orderLabel} · ${labels.value.metrics.riskScore} ${item.risk_score.toFixed(2)}`
  return {
    headline,
    detail: reviewQueueInsightDetail(item),
    reasons: riskReasonLabels(item).slice(0, 4),
  }
})

function reviewQueueOrderLabel() {
  if (reviewQueueOrder.value === 'position') return ''
  const metricLabels = labels.value.metrics
  const labelsByOrder: Record<string, string> = {
    random: metricLabels.random,
    uncertain: metricLabels.uncertain,
    goldsmith: metricLabels.goldsmith,
    hybrid: metricLabels.hybrid,
  }
  return labelsByOrder[reviewQueueOrder.value] ?? reviewQueueOrder.value
}

function reviewQueueInsightDetail(item: ReviewQueueItem) {
  const suggestion = item.first_suggestion
  const parts = [
    item.candidate_disagreement_score > 0 ? `${labels.value.metrics.candidateConflictRisk} ${item.candidate_disagreement_score.toFixed(2)}` : '',
    item.llm_review_risk_score > 0 ? `${labels.value.metrics.llmReviewRisk} ${item.llm_review_risk_score.toFixed(2)}` : '',
    item.judge_review_risk_score > 0 ? `${labels.value.metrics.judgeRisk} ${item.judge_review_risk_score.toFixed(2)}` : '',
    item.lexical_risk_score > 0 ? `${labels.value.metrics.lexicalRisk} ${item.lexical_risk_score.toFixed(2)}` : '',
  ].filter(Boolean)
  const firstCandidate = suggestion ? `${suggestion.tag_name}: ${suggestion.text}` : labels.value.metrics.queuePreviewFallback(item.suggestion_count)
  return parts.length ? `${firstCandidate} · ${parts.join(' · ')}` : firstCandidate
}

function riskReasonLabels(item: ReviewQueueItem) {
  const reasonLabels = labels.value.metrics.riskReasonLabels as Record<string, string>
  return item.risk_reason_codes.map((code) => reasonLabels[code] ?? code)
}
const localizedUndoLabel = computed(() => (canUndoSpanAction.value ? labels.value.reader.undoTitle : labels.value.reader.undoTitle))

async function confirmProjectReset() {
  if (!window.confirm(labels.value.metrics.resetConfirm)) return
  await resetProjectData()
}
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

      <div class="settings-menu">
        <button
          class="settings-button"
          type="button"
          :aria-label="labels.topbar.settings"
          :aria-expanded="isSettingsOpen"
          @click="isSettingsOpen = !isSettingsOpen"
        >
          <Settings :size="18" aria-hidden="true" />
          <span>{{ labels.topbar.modelSettings }}</span>
          <strong>{{ modelButtonLabel }}</strong>
        </button>
        <section v-if="isSettingsOpen" class="settings-popover" :aria-label="labels.topbar.modelSettingsTitle">
          <div class="settings-popover-heading">
            <p class="section-kicker">LLM</p>
            <h2>{{ labels.topbar.modelSettingsTitle }}</h2>
          </div>
          <p class="settings-description">{{ labels.topbar.modelSettingsDescription }}</p>
          <div class="settings-current-model">
            <span>{{ labels.topbar.currentModel }}</span>
            <strong>{{ modelButtonLabel }}</strong>
          </div>
          <div class="model-option-grid">
            <button
              v-for="option in llmSettings?.model_options ?? []"
              :key="option.id"
              type="button"
              class="model-option-button"
              :class="{ active: option.id === llmSettings?.selected_model_option_id }"
              :disabled="isSavingLlmSettings"
              @click="selectLlmOption(option.id)"
            >
              <span>
                <strong>{{ modelOptionLabel(option) }}</strong>
                <small>{{ qualityHint(option.tier) }}</small>
              </span>
              <em>{{ option.model }}</em>
            </button>
          </div>
          <p v-if="settingsError" class="settings-error">{{ settingsError }}</p>
          <p class="settings-note">{{ isSavingLlmSettings ? labels.topbar.modelSaving : settingsHint }}</p>
        </section>
      </div>
    </nav>

    <section class="workbench" :aria-label="labels.reader.aria">
      <TagPalette
        :labels="labels.tags"
        :tags="tags"
        :selected-tag-id="selectedTagId"
        :has-pending-selection="Boolean(pendingSelection)"
        :queue-items="queueItems"
        :current-sentence-index="currentSentenceIndex"
        :progress-percent="progressPercent"
        :reviewed-summary="reviewedSummary"
        :review-summary="localizedReviewSummary"
        :review-queue-summary="localizedReviewQueueSummary"
        :is-saving="isSaving"
        @tag-click="handleTagClick"
        @tag-add="addTag"
        @tag-rename="renameTag"
        @tag-delete="deleteTag"
        @tag-schema-import="handleTagSchemaImport"
        @tag-schema-export="exportTagSchemaJson"
        @sentence-click="setCurrentSentence"
      />

      <SentencePanel
        :labels="labels.reader"
        :sample-presets="samplePresets"
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
        :review-queue-insight="currentReviewQueueInsight"
        :reviewed-suggestion-count="metrics.reviewed_suggestion_count"
        :reader-error="readerError"
        :is-uploading="isUploading"
        :is-saving="isSaving"
        :is-suggesting="isSuggesting"
        :suggestion-limit="suggestionLimit"
        :suggestion-min-confidence="suggestionMinConfidence"
        :suggestion-reviews="suggestionReviews"
        :reviewing-suggestion-id="reviewingSuggestionId"
        :active-suggestion-target-id="activeSuggestionTargetId"
        :active-suggestion-position="activeSuggestionPosition"
        :annotation-for-token="annotationForToken"
        :suggestion-for-token="suggestionForToken"
        :is-token-in-drag="isTokenInDrag"
        :is-token-pending="isTokenPending"
        :token-prefix="tokenPrefix"
        :token-style="tokenStyle"
        @import="handleImport"
        @load-sample-preset="loadBuiltinSamplePreset"
        @document-change="switchDocument"
        @set-sentence-element="setSentenceElement"
        @sentence-click="onSentenceClick"
        @token-pointer-down="onTokenPointerDown"
        @token-pointer-enter="onTokenPointerEnter"
        @token-pointer-up="onTokenPointerUp"
        @select-current-sentence="selectCurrentSentenceSpan"
        @mark-current-monogloss="markCurrentSentenceMonogloss"
        @delete-annotation="removeAnnotation"
        @undo="undoLastSpanAction"
        @generate-current-suggestions="generateCurrentSentenceSuggestions"
        @generate-suggestions="generateDocumentSuggestions"
        @auto-annotate-document="autoAnnotateDocument"
        @accept-suggestion="acceptSuggestedSpan"
        @reject-suggestion="rejectSuggestedSpan"
        @suggestion-target="setActiveSuggestionTarget"
        @accept-current-suggestions="acceptCurrentSentenceSuggestions"
        @auto-accept-document-suggestions="autoAcceptDocumentSuggestions"
        @reject-current-suggestions="rejectCurrentSentenceSuggestions"
        @auto-reject-document-suggestions="autoRejectDocumentSuggestions"
        @review-suggestion="reviewSuggestedSpan"
        @review-current-suggestions="reviewCurrentSentenceSuggestions"
        @apply-current-reviews="applyCurrentSentenceSuggestionReviews"
        @apply-document-reviews="applyDocumentSuggestionReviewsFromLlm"
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
        :last-annotation-import="lastAnnotationImport"
        :is-verifying-rebuild="isVerifyingRebuild"
        :is-resetting="isResetting"
        @export="exportJsonl"
        @export-prodigy="exportProdigyJsonl"
        @export-prodigy-bundle="exportProdigyBundleZip"
        @export-prodigy-spans="exportProdigySpansJsonl"
        @export-prodigy-labels="exportProdigyLabelsJson"
        @import-annotations="handleAnnotationImport"
        @review-sentence="setCurrentSentence"
        @review-order-change="setReviewQueueOrder"
        @export-manifest="exportManifestJson"
        @export-events="exportEventsJsonl"
        @export-tag-schema="exportTagSchemaJson"
        @export-run-provenance="exportRunProvenanceJson"
        @export-goldsmith-review-queue="exportGoldsmithReviewQueueJsonl"
        @export-goldsmith-human-choices="exportGoldsmithHumanChoicesJsonl"
        @export-goldsmith-hard-examples="exportGoldsmithHardExamplesJsonl"
        @export-goldsmith-boundary-feedback="exportGoldsmithBoundaryFeedbackJsonl"
        @export-goldsmith-consistency-scores="exportGoldsmithConsistencyScoresJsonl"
        @export-goldsmith-candidate-runs="exportGoldsmithCandidateRunsJsonl"
        @export-goldsmith-risk-reasons="exportGoldsmithRiskReasonsJsonl"
        @export-goldsmith-review-tasks="exportGoldsmithReviewTasksJsonl"
        @verify-rebuild="verifyRebuildPreview"
        @reset-project="confirmProjectReset"
      />
    </section>
  </main>
</template>
