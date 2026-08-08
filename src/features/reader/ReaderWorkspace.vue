<script setup lang="ts">
import { CheckCircle, Settings } from '@lucide/vue'
import { useDocumentReader } from '../../composables/useDocumentReader'
import MetricsPanel from './MetricsPanel.vue'
import SentencePanel from './SentencePanel.vue'
import TagPalette from './TagPalette.vue'

const {
  tags,
  documentMeta,
  sentences,
  metrics,
  selectedTagId,
  currentSentenceIndex,
  isUploading,
  isSaving,
  readerError,
  currentSentence,
  progressPercent,
  reviewedSummary,
  activeAnnotations,
  queueItems,
  pendingSelection,
  pendingSelectionText,
  handleImport,
  setCurrentSentence,
  completeCurrentSentence,
  setSentenceElement,
  onSentenceClick,
  onTokenPointerDown,
  onTokenPointerEnter,
  onTokenPointerUp,
  handleTagClick,
  removeAnnotation,
  annotationForToken,
  isTokenInDrag,
  isTokenPending,
  tokenPrefix,
  tokenStyle,
  exportJsonl,
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

      <div class="run-status" aria-label="Runtime status">
        <CheckCircle :size="17" aria-hidden="true" />
        <span>SQLite + JSONL</span>
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
        @tag-click="handleTagClick"
        @sentence-click="setCurrentSentence"
      />

      <SentencePanel
        :document-meta="documentMeta"
        :current-sentence="currentSentence"
        :current-sentence-index="currentSentenceIndex"
        :sentences="sentences"
        :active-annotations="activeAnnotations"
        :pending-selection="pendingSelection"
        :pending-selection-text="pendingSelectionText"
        :reader-error="readerError"
        :is-uploading="isUploading"
        :is-saving="isSaving"
        :annotation-for-token="annotationForToken"
        :is-token-in-drag="isTokenInDrag"
        :is-token-pending="isTokenPending"
        :token-prefix="tokenPrefix"
        :token-style="tokenStyle"
        @import="handleImport"
        @set-sentence-element="setSentenceElement"
        @sentence-click="onSentenceClick"
        @token-pointer-down="onTokenPointerDown"
        @token-pointer-enter="onTokenPointerEnter"
        @token-pointer-up="onTokenPointerUp"
        @delete-annotation="removeAnnotation"
        @complete="completeCurrentSentence"
        @previous="setCurrentSentence(currentSentenceIndex - 1)"
        @next="setCurrentSentence(currentSentenceIndex + 1)"
      />

      <MetricsPanel
        :document-meta="documentMeta"
        :metrics="metrics"
        :progress-percent="progressPercent"
        :reviewed-summary="reviewedSummary"
        @export="exportJsonl"
      />
    </section>
  </main>
</template>
