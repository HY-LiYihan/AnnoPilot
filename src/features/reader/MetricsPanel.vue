<script setup lang="ts">
import { BarChart3, DatabaseZap, Download, Gauge, Keyboard, MousePointer2, RotateCw, Route, Sparkles, Target, Upload } from '@lucide/vue'
import type { AnnotationRun, AuditSummary, DocumentMeta, Metrics, RebuildPreview, ReviewQueueItem } from '../../types/domain'

defineProps<{
  documentMeta: DocumentMeta | null
  metrics: Metrics
  auditSummary: AuditSummary | null
  rebuildPreview: RebuildPreview | null
  runHistory: AnnotationRun[]
  reviewQueueDetails: ReviewQueueItem[]
  reviewQueueTotal: number
  progressPercent: number
  reviewedSummary: string
  isVerifyingRebuild: boolean
}>()

const emit = defineEmits<{
  export: []
  'export-prodigy': []
  'export-manifest': []
  'export-events': []
  'export-tag-schema': []
  'export-run-provenance': [runId: string]
  'import-annotations': [file: File]
  'review-sentence': [sentenceIndex: number]
  'verify-rebuild': []
}>()

function runRate(run: AnnotationRun) {
  return run.acceptance_rate === null ? '--' : `${Math.round(run.acceptance_rate * 100)}%`
}

function runLimit(run: AnnotationRun) {
  return typeof run.config.limit_per_sentence === 'number' ? run.config.limit_per_sentence : '--'
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

function queuePreviewText(item: ReviewQueueItem) {
  const suggestion = item.first_suggestion
  return suggestion ? `${suggestion.tag_name}: ${suggestion.text}` : `${item.suggestion_count} pending suggestions`
}
</script>

<template>
  <aside class="side-panel stats-panel" aria-labelledby="stats-panel-title">
    <div class="panel-heading">
      <div>
        <p class="section-kicker">Evidence</p>
        <h2 id="stats-panel-title">Run metrics</h2>
      </div>
      <BarChart3 :size="21" aria-hidden="true" />
    </div>

    <div class="metric-stack">
      <article class="metric-card">
        <Gauge :size="22" aria-hidden="true" />
        <span>Progress</span>
        <strong>{{ progressPercent }}%</strong>
        <small>{{ reviewedSummary }} sentences</small>
      </article>
      <article class="metric-card">
        <Target :size="22" aria-hidden="true" />
        <span>Accuracy</span>
        <strong>{{ metrics.accuracy === null ? '--' : `${Math.round(metrics.accuracy * 100)}%` }}</strong>
        <small>{{ metrics.accuracy_label }}</small>
      </article>
      <article class="metric-card">
        <MousePointer2 :size="22" aria-hidden="true" />
        <span>Annotated spans</span>
        <strong>{{ metrics.annotation_count }}</strong>
        <small>Persisted to SQLite</small>
      </article>
      <article class="metric-card">
        <Sparkles :size="22" aria-hidden="true" />
        <span>Suggestions</span>
        <strong>{{ metrics.suggestion_count }}</strong>
        <small>Character RAG queue</small>
      </article>
      <article class="metric-card" :class="{ warning: auditSummary?.rebuild_status === 'needs_attention' }">
        <DatabaseZap :size="22" aria-hidden="true" />
        <span>Audit Log</span>
        <strong>{{ auditSummary?.event_count ?? 0 }}</strong>
        <small>
          {{ auditSummary?.rebuild_status ?? 'not checked' }} · {{ auditSummary?.pending_outbox_count ?? 0 }} pending ·
          {{ auditSummary?.non_replayable_event_count ?? 0 }} replay gaps
        </small>
        <div v-if="auditSummary" class="actor-breakdown" aria-label="Audit actor distribution">
          <span>H {{ actorCount(auditSummary, 'human') }}</span>
          <span>S {{ actorCount(auditSummary, 'system') }}</span>
          <span>L {{ actorCount(auditSummary, 'llm') }}</span>
        </div>
        <div v-if="auditSummary?.replay_issues?.length" class="audit-issue-list" aria-label="Audit replay issue samples">
          <small v-for="issue in auditSummary.replay_issues.slice(0, 3)" :key="`${issue.line_number}-${issue.message}`">
            #{{ issue.line_number }} {{ issue.event_type ?? 'event' }} · {{ issue.message }}
          </small>
        </div>
      </article>
      <article class="metric-card">
        <Route :size="22" aria-hidden="true" />
        <span>Last Run</span>
        <strong>{{ runHistory[0]?.suggestion_count ?? 0 }}</strong>
        <small>
          {{ runHistory[0]?.recipe ?? 'no run' }}
          <template v-if="runHistory[0]?.config?.limit_per_sentence"> · limit {{ runHistory[0].config.limit_per_sentence }}</template>
          <template v-if="runHistory[0]">
            · {{ runHistory[0].accepted_count }} accepted / {{ runHistory[0].rejected_count }} rejected / {{ runHistory[0].pending_count }} pending
          </template>
        </small>
      </article>
    </div>

    <section class="progress-card" aria-label="Annotation progress">
      <div class="progress-header">
        <span>Document</span>
        <strong>{{ documentMeta?.sentence_count ?? 0 }} sentences</strong>
      </div>
      <div class="progress-track" aria-hidden="true">
        <span :style="{ width: `${progressPercent}%` }"></span>
      </div>
      <div class="quality-list">
        <div>
          <span>Accept</span>
          <strong>{{ metrics.answer_counts.accept ?? 0 }}</strong>
        </div>
        <div>
          <span>Reject</span>
          <strong>{{ metrics.answer_counts.reject ?? 0 }}</strong>
        </div>
        <div>
          <span>Ignore</span>
          <strong>{{ metrics.answer_counts.ignore ?? 0 }}</strong>
        </div>
        <div>
          <span>Pending</span>
          <strong>{{ metrics.answer_counts.pending ?? 0 }}</strong>
        </div>
        <div>
          <span>Tokens</span>
          <strong>{{ documentMeta?.token_count ?? 0 }}</strong>
        </div>
      </div>
    </section>

    <section class="progress-card shortcut-card" aria-label="Keyboard and gesture shortcuts">
      <div class="progress-header">
        <span>Shortcuts</span>
        <strong><Keyboard :size="17" aria-hidden="true" /> fast mode</strong>
      </div>
      <div class="shortcut-grid">
        <span><kbd>1-9</kbd><em>apply tag</em></span>
        <span><kbd>Enter</kbd><em>complete sentence</em></span>
        <span><kbd>Space / I</kbd><em>ignore sentence</em></span>
        <span><kbd>J</kbd><em>reject sentence</em></span>
        <span><kbd>E</kbd><em>reopen sentence</em></span>
        <span><kbd>Y / N</kbd><em>accept/reject first suggestion</em></span>
        <span><kbd>A</kbd><em>accept suggestions</em></span>
        <span><kbd>X</kbd><em>reject suggestions</em></span>
        <span><kbd>R</kbd><em>next review</em></span>
        <span><kbd>⌘/Ctrl Z</kbd><em>undo span</em></span>
        <span><kbd>↑ / ↓</kbd><em>move sentence</em></span>
        <span><kbd>Swipe</kbd><em>mobile next/prev</em></span>
      </div>
    </section>

    <section v-if="runHistory.length" class="progress-card run-history-card" aria-label="Recent Character RAG runs">
      <div class="progress-header">
        <span>Run History</span>
        <strong>{{ runHistory.length }} recent</strong>
      </div>
      <div class="run-history-list">
        <article v-for="run in runHistory.slice(0, 3)" :key="run.id" class="run-history-row">
          <span>
            <strong>{{ run.recipe }}</strong>
            <small>{{ run.id.slice(0, 10) }} · limit {{ runLimit(run) }} · accept {{ runRate(run) }}</small>
          </span>
          <div class="run-history-pills" aria-label="Run status counts">
            <em class="accepted">{{ run.accepted_count }} A</em>
            <em class="rejected">{{ run.rejected_count }} R</em>
            <em>{{ run.pending_count }} P</em>
          </div>
          <button class="run-export-button" type="button" title="Export run provenance" @click="emit('export-run-provenance', run.id)">
            <Download :size="15" aria-hidden="true" />
          </button>
        </article>
      </div>
    </section>

    <section v-if="reviewQueueTotal" class="progress-card review-queue-card" aria-label="Review queue">
      <div class="progress-header">
        <span>Review Queue</span>
        <strong>{{ reviewQueueTotal }} pending</strong>
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
            <strong>#{{ item.index + 1 }} · {{ item.suggestion_count }} suggestions</strong>
            <small>{{ queuePreviewText(item) }}</small>
          </span>
          <em>Go</em>
        </button>
      </div>
    </section>

    <section v-if="rebuildPreview" class="progress-card rebuild-card" :class="{ warning: !rebuildPreview.ok }" aria-label="Rebuild preview">
      <div class="progress-header">
        <span>Rebuild Preview</span>
        <strong>{{ rebuildPreview.ok ? 'ready' : 'gaps' }}</strong>
      </div>
      <div class="quality-list">
        <div>
          <span>Documents</span>
          <strong>{{ rebuildPreview.documents }}</strong>
        </div>
        <div>
          <span>Runs</span>
          <strong>{{ rebuildPreview.runs }}</strong>
        </div>
        <div>
          <span>Issues</span>
          <strong>{{ rebuildPreview.issues.length }}</strong>
        </div>
        <div>
          <span>Events</span>
          <strong>{{ rebuildPreview.event_count }}</strong>
        </div>
      </div>
      <div v-if="rebuildPreview.issues.length" class="audit-issue-list rebuild-issue-list" aria-label="Rebuild issue samples">
        <small v-for="issue in rebuildPreview.issues.slice(0, 3)" :key="`${issue.line_number}-${issue.message}`">
          #{{ issue.line_number }} {{ issue.event_type ?? 'event' }} · {{ issue.message }}
        </small>
      </div>
    </section>

    <button class="export-button" :disabled="!documentMeta" @click="emit('export')">
      Export Tasks
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-prodigy')">
      Export Prodigy
      <Download :size="18" aria-hidden="true" />
    </button>

    <label class="export-button secondary import-jsonl-button" :class="{ disabled: !documentMeta }">
      Import JSONL
      <Upload :size="18" aria-hidden="true" />
      <input type="file" accept=".jsonl,application/x-ndjson,application/jsonl" :disabled="!documentMeta" @change="submitAnnotationImport" />
    </label>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-manifest')">
      Export Manifest
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" @click="emit('export-events')">
      Export Events
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" @click="emit('export-tag-schema')">
      Export Tag Schema
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="isVerifyingRebuild" @click="emit('verify-rebuild')">
      {{ isVerifyingRebuild ? 'Verifying...' : 'Verify Rebuild' }}
      <RotateCw :size="18" aria-hidden="true" />
    </button>
  </aside>
</template>
