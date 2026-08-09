<script setup lang="ts">
import { BarChart3, DatabaseZap, Download, Keyboard, MousePointer2, RotateCw, Route, Sparkles, Target, Upload } from '@lucide/vue'
import type { UiLabels } from '../../i18n'
import type { AnnotationImportSummary, AnnotationRun, AuditSummary, DocumentMeta, Metrics, RebuildPreview, ReviewQueueItem } from '../../types/domain'

defineProps<{
  labels: UiLabels['metrics']
  documentMeta: DocumentMeta | null
  metrics: Metrics
  auditSummary: AuditSummary | null
  rebuildPreview: RebuildPreview | null
  runHistory: AnnotationRun[]
  reviewQueueDetails: ReviewQueueItem[]
  reviewQueueTotal: number
  reviewQueueOrder: 'position' | 'uncertain'
  lastAnnotationImport: AnnotationImportSummary | null
  isVerifyingRebuild: boolean
}>()

const emit = defineEmits<{
  export: []
  'export-prodigy': []
  'export-prodigy-spans': []
  'export-manifest': []
  'export-events': []
  'export-tag-schema': []
  'export-run-provenance': [runId: string]
  'import-annotations': [file: File]
  'review-sentence': [sentenceIndex: number]
  'review-order-change': [order: 'position' | 'uncertain']
  'verify-rebuild': []
}>()

function runRate(run: AnnotationRun) {
  return run.acceptance_rate === null ? '--' : `${Math.round(run.acceptance_rate * 100)}%`
}

function runLimit(run: AnnotationRun) {
  return typeof run.config.limit_per_sentence === 'number' ? run.config.limit_per_sentence : '--'
}

function runSourceSummary(run: AnnotationRun, labels: UiLabels['metrics']) {
  const sourceCounts = Object.entries(run.source_counts ?? {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
  if (!sourceCounts.length) return ''
  const sourceLabels = labels.sourceLabels as Record<string, string>
  return sourceCounts
    .map(([source, count]) => `${sourceLabels[source] ?? source} ${count}`)
    .join(' · ')
}

function actorCount(auditSummary: AuditSummary | null, actorType: string) {
  return auditSummary?.actor_type_counts?.[actorType] ?? 0
}

function submitAnnotationImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  emit('import-annotations', file)
  input.value = ''
}

function accuracyLabel(value: string, labels: UiLabels['metrics']) {
  return value === 'Waiting for review data' ? labels.waitingReviewData : value
}

function queuePreviewText(item: ReviewQueueItem, labels: UiLabels['metrics']) {
  const suggestion = item.first_suggestion
  const confidence = `${Math.round(item.priority_score * 100)}%`
  return suggestion ? `${suggestion.tag_name}: ${suggestion.text} · ${confidence}` : labels.queuePreviewFallback(item.suggestion_count)
}

function shortHash(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value
}
</script>

<template>
  <aside class="side-panel stats-panel" aria-labelledby="stats-panel-title" :aria-label="labels.aria">
    <div class="panel-heading">
      <div>
        <p class="section-kicker">{{ labels.kicker }}</p>
        <h2 id="stats-panel-title">{{ labels.title }}</h2>
      </div>
      <BarChart3 :size="21" aria-hidden="true" />
    </div>

    <div class="metric-stack">
      <article class="metric-card">
        <Target :size="22" aria-hidden="true" />
        <span>{{ labels.accuracy }}</span>
        <strong>{{ metrics.accuracy === null ? '--' : `${Math.round(metrics.accuracy * 100)}%` }}</strong>
        <small>{{ accuracyLabel(metrics.accuracy_label, labels) }}</small>
      </article>
      <article class="metric-card">
        <MousePointer2 :size="22" aria-hidden="true" />
        <span>{{ labels.annotatedSpans }}</span>
        <strong>{{ metrics.annotation_count }}</strong>
        <small>{{ labels.persisted }}</small>
      </article>
      <article class="metric-card">
        <Sparkles :size="22" aria-hidden="true" />
        <span>{{ labels.suggestions }}</span>
        <strong>{{ metrics.suggestion_count }}</strong>
        <small>{{ labels.ragQueue }}</small>
      </article>
      <article class="metric-card" :class="{ warning: auditSummary?.rebuild_status === 'needs_attention' }">
        <DatabaseZap :size="22" aria-hidden="true" />
        <span>{{ labels.auditLog }}</span>
        <strong>{{ auditSummary?.event_count ?? 0 }}</strong>
        <small>
          {{ auditSummary?.rebuild_status ?? labels.notChecked }} · {{ auditSummary?.pending_outbox_count ?? 0 }} {{ labels.pending }} ·
          {{ auditSummary?.non_replayable_event_count ?? 0 }} {{ labels.replayGaps }}
        </small>
        <div v-if="auditSummary" class="actor-breakdown" :aria-label="labels.actorDistributionAria">
          <span>H {{ actorCount(auditSummary, 'human') }}</span>
          <span>S {{ actorCount(auditSummary, 'system') }}</span>
          <span>L {{ actorCount(auditSummary, 'llm') }}</span>
        </div>
        <div v-if="auditSummary?.replay_issues?.length" class="audit-issue-list" :aria-label="labels.replayIssueAria">
          <small v-for="issue in auditSummary.replay_issues.slice(0, 3)" :key="`${issue.line_number}-${issue.message}`">
            #{{ issue.line_number }} {{ issue.event_type ?? labels.event }} · {{ issue.message }}
          </small>
        </div>
      </article>
      <article class="metric-card">
        <Route :size="22" aria-hidden="true" />
        <span>{{ labels.lastRun }}</span>
        <strong>{{ runHistory[0]?.suggestion_count ?? 0 }}</strong>
        <small>
          {{ runHistory[0]?.recipe ?? labels.noRun }}
          <template v-if="runHistory[0]?.config?.limit_per_sentence"> · {{ labels.limit }} {{ runHistory[0].config.limit_per_sentence }}</template>
          <template v-if="runHistory[0]">
            · {{ runHistory[0].accepted_count }} {{ labels.accepted }} / {{ runHistory[0].rejected_count }} {{ labels.rejected }} / {{ runHistory[0].pending_count }} {{ labels.pendingStatus }}
          </template>
          <template v-if="runHistory[0] && runSourceSummary(runHistory[0], labels)"> · {{ labels.sourceMix }} {{ runSourceSummary(runHistory[0], labels) }}</template>
        </small>
      </article>
    </div>

    <section class="progress-card shortcut-card" :aria-label="labels.shortcuts">
      <div class="progress-header">
        <span>{{ labels.shortcuts }}</span>
        <strong><Keyboard :size="17" aria-hidden="true" /> {{ labels.fastMode }}</strong>
      </div>
      <div class="shortcut-grid">
        <span><kbd>1-9</kbd><em>{{ labels.applyTag }}</em></span>
        <span><kbd>Enter</kbd><em>{{ labels.completeSentence }}</em></span>
        <span><kbd>Space / I</kbd><em>{{ labels.ignoreSentence }}</em></span>
        <span><kbd>J</kbd><em>{{ labels.rejectSentence }}</em></span>
        <span><kbd>E</kbd><em>{{ labels.reopenSentence }}</em></span>
        <span><kbd>Y / N</kbd><em>{{ labels.firstSuggestion }}</em></span>
        <span><kbd>A</kbd><em>{{ labels.acceptSuggestions }}</em></span>
        <span><kbd>X</kbd><em>{{ labels.rejectSuggestions }}</em></span>
        <span><kbd>R</kbd><em>{{ labels.nextReview }}</em></span>
        <span><kbd>⌘/Ctrl Z</kbd><em>{{ labels.undoSpan }}</em></span>
        <span><kbd>↑ / ↓</kbd><em>{{ labels.moveSentence }}</em></span>
        <span><kbd>Swipe</kbd><em>{{ labels.mobileSwipe }}</em></span>
      </div>
    </section>

    <section v-if="runHistory.length" class="progress-card run-history-card" :aria-label="labels.runHistory">
      <div class="progress-header">
        <span>{{ labels.runHistory }}</span>
        <strong>{{ runHistory.length }} {{ labels.recent }}</strong>
      </div>
      <div class="run-history-list">
        <article v-for="run in runHistory.slice(0, 3)" :key="run.id" class="run-history-row">
          <span>
            <strong>{{ run.recipe }}</strong>
            <small>
              {{ run.id.slice(0, 10) }} · {{ labels.limit }} {{ runLimit(run) }} · {{ labels.acceptRate }} {{ runRate(run) }}
              <template v-if="runSourceSummary(run, labels)"> · {{ labels.sourceMix }} {{ runSourceSummary(run, labels) }}</template>
            </small>
          </span>
          <div class="run-history-pills" :aria-label="labels.runStatusAria">
            <em class="accepted">{{ run.accepted_count }} A</em>
            <em class="rejected">{{ run.rejected_count }} R</em>
            <em>{{ run.pending_count }} P</em>
          </div>
          <button class="run-export-button" type="button" :title="labels.exportRunTitle" @click="emit('export-run-provenance', run.id)">
            <Download :size="15" aria-hidden="true" />
          </button>
        </article>
      </div>
    </section>

    <section v-if="reviewQueueTotal" class="progress-card review-queue-card" :aria-label="labels.reviewQueue">
      <div class="progress-header">
        <span>{{ labels.reviewQueue }}</span>
        <strong>{{ reviewQueueTotal }} {{ labels.pending }}</strong>
      </div>
      <div class="review-order-toggle" :aria-label="labels.reviewQueue">
        <button type="button" :class="{ active: reviewQueueOrder === 'position' }" @click="emit('review-order-change', 'position')">
          {{ labels.position }}
        </button>
        <button type="button" :class="{ active: reviewQueueOrder === 'uncertain' }" @click="emit('review-order-change', 'uncertain')">
          {{ labels.uncertain }}
        </button>
      </div>
      <div class="review-queue-list">
        <button
          v-for="item in reviewQueueDetails.slice(0, 5)"
          :key="item.id"
          class="review-queue-row"
          type="button"
          @click="emit('review-sentence', item.index)"
        >
          <span>
            <strong>#{{ item.index + 1 }} · {{ labels.suggestionsCount(item.suggestion_count) }}</strong>
            <small>{{ queuePreviewText(item, labels) }}</small>
          </span>
          <em>{{ labels.go }}</em>
        </button>
      </div>
    </section>

    <section v-if="rebuildPreview" class="progress-card rebuild-card" :class="{ warning: !rebuildPreview.ok }" :aria-label="labels.rebuildPreview">
      <div class="progress-header">
        <span>{{ labels.rebuildPreview }}</span>
        <strong>{{ rebuildPreview.ok ? labels.ready : labels.gaps }}</strong>
      </div>
      <div class="quality-list">
        <div>
          <span>{{ labels.documents }}</span>
          <strong>{{ rebuildPreview.documents }}</strong>
        </div>
        <div>
          <span>{{ labels.runs }}</span>
          <strong>{{ rebuildPreview.runs }}</strong>
        </div>
        <div>
          <span>{{ labels.issues }}</span>
          <strong>{{ rebuildPreview.issues.length }}</strong>
        </div>
        <div>
          <span>{{ labels.events }}</span>
          <strong>{{ rebuildPreview.event_count }}</strong>
        </div>
      </div>
      <div v-if="rebuildPreview.issues.length" class="audit-issue-list rebuild-issue-list" :aria-label="labels.rebuildIssueAria">
        <small v-for="issue in rebuildPreview.issues.slice(0, 3)" :key="`${issue.line_number}-${issue.message}`">
          #{{ issue.line_number }} {{ issue.event_type ?? labels.event }} · {{ issue.message }}
        </small>
      </div>
    </section>

    <button class="export-button" :disabled="!documentMeta" @click="emit('export')">
      {{ labels.exportTasks }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-prodigy')">
      {{ labels.exportProdigy }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-prodigy-spans')">
      {{ labels.exportProdigySpans }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <label class="export-button secondary import-jsonl-button" :class="{ disabled: !documentMeta }">
      {{ labels.importJsonl }}
      <Upload :size="18" aria-hidden="true" />
      <input type="file" accept=".jsonl,application/x-ndjson,application/jsonl" :disabled="!documentMeta" @change="submitAnnotationImport" />
    </label>

    <section v-if="lastAnnotationImport" class="progress-card import-result-card" :aria-label="labels.importResultAria">
      <div class="progress-header">
        <span>{{ labels.importResult }}</span>
        <strong>{{ labels.importMatched(lastAnnotationImport.matched_count, lastAnnotationImport.record_count) }}</strong>
      </div>
      <small class="import-source-line" :title="lastAnnotationImport.import_filename">
        {{ labels.importSource }} · {{ lastAnnotationImport.import_filename }}
      </small>
      <div class="quality-list compact">
        <div>
          <span>{{ labels.importSkipped }}</span>
          <strong>{{ lastAnnotationImport.skipped_count }}</strong>
        </div>
        <div>
          <span>{{ labels.importTags }}</span>
          <strong>{{ lastAnnotationImport.created_tag_count }}</strong>
        </div>
        <div>
          <span>{{ labels.importCreatedSpans }}</span>
          <strong>{{ lastAnnotationImport.created_annotation_count }}</strong>
        </div>
        <div>
          <span>{{ labels.importDeletedSpans }}</span>
          <strong>{{ lastAnnotationImport.deleted_annotation_count }}</strong>
        </div>
      </div>
      <small class="import-source-line" :title="lastAnnotationImport.source_sha256">
        {{ labels.sourceHash }} · {{ shortHash(lastAnnotationImport.source_sha256) }}
      </small>
    </section>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-manifest')">
      {{ labels.exportManifest }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" @click="emit('export-events')">
      {{ labels.exportEvents }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="isVerifyingRebuild" @click="emit('verify-rebuild')">
      {{ isVerifyingRebuild ? labels.verifying : labels.verifyRebuild }}
      <RotateCw :size="18" aria-hidden="true" />
    </button>
  </aside>
</template>
