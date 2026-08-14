<script setup lang="ts">
import { BarChart3, DatabaseZap, Download, Keyboard, MousePointer2, RotateCw, Route, Sparkles, Target, Trash2, Upload } from '@lucide/vue'
import {
  annotationOverlapCount,
  buildReadinessActions,
  pendingSuggestionCount,
  type ReadinessAction,
} from '../../composables/readerReadinessActions'
import type { UiLabels } from '../../i18n'
import type { AnnotationImportSummary, AnnotationRun, AuditSummary, DocumentMeta, LabelCount, Metrics, RebuildPreview, ReviewQueueItem, ReviewQueueOrder, SentenceQueueItem } from '../../types/domain'

defineProps<{
  labels: UiLabels['metrics']
  documentMeta: DocumentMeta | null
  metrics: Metrics
  auditSummary: AuditSummary | null
  rebuildPreview: RebuildPreview | null
  runHistory: AnnotationRun[]
  queueItems: SentenceQueueItem[]
  reviewQueueDetails: ReviewQueueItem[]
  reviewQueueTotal: number
  reviewQueueOrder: ReviewQueueOrder
  lastAnnotationImport: AnnotationImportSummary | null
  isVerifyingRebuild: boolean
  isSaving: boolean
  isResetting: boolean
}>()

const emit = defineEmits<{
  export: []
  'export-prodigy': []
  'export-prodigy-bundle': []
  'export-prodigy-spans': []
  'export-prodigy-labels': []
  'export-manifest': []
  'export-events': []
  'export-tag-schema': []
  'export-run-provenance': [runId: string]
  'export-goldsmith-bootstrap-report': []
  'export-goldsmith-review-queue': []
  'export-goldsmith-human-choices': []
  'export-goldsmith-hard-examples': []
  'export-goldsmith-boundary-feedback': []
  'export-goldsmith-consistency-scores': []
  'export-goldsmith-candidate-runs': []
  'export-goldsmith-contrastive-examples': []
  'export-goldsmith-prompt-package': []
  'export-goldsmith-reflection-plans': []
  'export-goldsmith-risk-reasons': []
  'export-goldsmith-label-statistics': []
  'export-goldsmith-review-tasks': []
  'export-goldsmith-verification-report': []
  'auto-mark-monogloss': []
  'import-annotations': [file: File]
  'reset-project': []
  'review-sentence': [sentenceIndex: number]
  'review-order-change': [order: ReviewQueueOrder]
  'verify-rebuild': []
}>()

function runRate(run: AnnotationRun) {
  return run.acceptance_rate === null ? '--' : `${Math.round(run.acceptance_rate * 100)}%`
}

function runLimit(run: AnnotationRun) {
  return typeof run.config.limit_per_sentence === 'number' ? run.config.limit_per_sentence : '--'
}

function runSourceSummary(run: AnnotationRun, labels: UiLabels['metrics']) {
  return sourceSummary(run.source_counts, labels)
}

function sourceSummary(counts: Record<string, number> | undefined, labels: UiLabels['metrics']) {
  const sourceCounts = Object.entries(counts ?? {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
  if (!sourceCounts.length) return ''
  const sourceLabels = labels.sourceLabels as Record<string, string>
  return sourceCounts
    .map(([source, count]) => `${sourceLabels[source] ?? source} ${count}`)
    .join(' · ')
}

function runConfidenceSummary(run: AnnotationRun, labels: UiLabels['metrics']) {
  return confidenceSummary(run.confidence_counts, labels)
}

function suggestionStatusSummary(counts: Record<string, number> | undefined, labels: UiLabels['metrics']) {
  const pending = counts?.pending ?? 0
  const accepted = counts?.accepted ?? 0
  const rejected = counts?.rejected ?? 0
  if (!pending && !accepted && !rejected) return ''
  return `${accepted} ${labels.accepted} / ${rejected} ${labels.rejected} / ${pending} ${labels.pendingStatus}`
}

function reviewSummary(counts: Record<string, number> | undefined, labels: UiLabels['metrics']) {
  const reviewCounts = Object.entries(counts ?? {})
    .filter(([, count]) => count > 0)
    .sort(([left], [right]) => reviewOrder(left) - reviewOrder(right))
  if (!reviewCounts.length) return ''
  const reviewLabels = labels.reviewLabels as Record<string, string>
  return reviewCounts.map(([recommendation, count]) => `${reviewLabels[recommendation] ?? recommendation} ${count}`).join(' · ')
}

function efficiencySummary(metrics: Metrics, labels: UiLabels['metrics']) {
  const curves = metrics.review_efficiency_curves ?? {}
  const orderLabels: Array<[string, string]> = [
    ['random', labels.random],
    ['goldsmith', labels.goldsmith],
    ['hybrid', labels.hybrid],
  ]
  const parts = orderLabels
    .map(([order, label]) => {
      const curve = curves[order]
      if (!curve?.early_reviewed_count) return ''
      return `${label} ${curve.early_disagreement_count}/${curve.early_reviewed_count}`
    })
    .filter(Boolean)
  const topReason = topDisagreementReason(curves.goldsmith?.disagreement_reason_counts, labels)
  if (topReason) parts.push(topReason)
  return parts.length ? `${labels.errorDiscovery}: ${parts.join(' · ')}` : ''
}

function topDisagreementReason(counts: Record<string, number> | undefined, labels: UiLabels['metrics']) {
  const [topReason, count] = Object.entries(counts ?? {}).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0] ?? []
  if (!topReason || !count) return ''
  const reasonLabels = labels.riskReasonLabels as Record<string, string>
  return `${labels.topRiskReason} ${reasonLabels[topReason] ?? topReason} ${count}`
}

function confidenceSummary(counts: Record<string, number> | undefined, labels: UiLabels['metrics']) {
  const confidenceCounts = Object.entries(counts ?? {})
    .filter(([, count]) => count > 0)
    .sort(([leftBucket], [rightBucket]) => confidenceBucketOrder(leftBucket) - confidenceBucketOrder(rightBucket))
  if (!confidenceCounts.length) return ''
  const confidenceLabels = labels.confidenceLabels as Record<string, string>
  return confidenceCounts
    .map(([bucket, count]) => `${confidenceLabels[bucket] ?? bucket} ${count}`)
    .join(' · ')
}

function confidenceBucketOrder(bucket: string) {
  return { high: 0, medium: 1, low: 2 }[bucket as 'high' | 'medium' | 'low'] ?? 3
}

function visibleLabelCounts(counts: LabelCount[], limit = 9) {
  return counts.slice(0, limit)
}

function labelMixTitle(item: LabelCount, labels: UiLabels['metrics']) {
  return `${item.name}: ${labels.labelCount(item.count)}`
}

function coveredLabelCount(metrics: Metrics) {
  return metrics.annotation_label_counts.filter((item) => item.count > 0).length
}

function totalLabelCount(metrics: Metrics) {
  return metrics.annotation_label_counts.length
}

function annotationConflictItems(queueItems: SentenceQueueItem[]) {
  return queueItems.filter((item) => (item.annotation_overlap_count ?? 0) > 0).slice(0, 5)
}

function readinessActions(
  documentMeta: DocumentMeta | null,
  metrics: Metrics,
  queueItems: SentenceQueueItem[],
  reviewQueueDetails: ReviewQueueItem[],
) {
  return buildReadinessActions(documentMeta, metrics, queueItems, reviewQueueDetails)
}

function readinessActionTitle(action: ReadinessAction, labels: UiLabels['metrics']) {
  if (action.id === 'annotation_conflicts') return labels.readinessConflict(action.count)
  if (action.id === 'pending_suggestions') return labels.readinessPending(action.count)
  if (action.id === 'incomplete_sentences') return labels.readinessIncomplete(action.count)
  if (action.id === 'no_annotations') return labels.readinessNoAnnotations
  return labels.readinessAutoMonogloss
}

function readinessActionDetail(action: ReadinessAction, labels: UiLabels['metrics']) {
  if (action.kind === 'auto-mark-monogloss') return labels.readinessAutoMonoglossHint
  if (action.targetSentenceIndex === null) return labels.readinessNoTarget
  return labels.readinessJumpTarget(action.targetSentenceIndex + 1)
}

function readinessActionCta(action: ReadinessAction, labels: UiLabels['metrics']) {
  return action.kind === 'auto-mark-monogloss' ? labels.run : labels.go
}

function readinessActionDisabled(action: ReadinessAction, isSaving: boolean) {
  if (action.kind === 'auto-mark-monogloss') return isSaving
  return action.targetSentenceIndex === null
}

function handleReadinessAction(action: ReadinessAction) {
  if (action.kind === 'auto-mark-monogloss') {
    emit('auto-mark-monogloss')
    return
  }
  if (action.targetSentenceIndex !== null) emit('review-sentence', action.targetSentenceIndex)
}

function progressPercent(metrics: Metrics) {
  return (metrics.progress * 100).toFixed(2)
}

function isProdigyExportReady(documentMeta: DocumentMeta | null, metrics: Metrics) {
  return Boolean(
    documentMeta
      && metrics.sentence_count > 0
      && metrics.completed_count >= metrics.sentence_count
      && pendingSuggestionCount(metrics) === 0
      && annotationOverlapCount(metrics) === 0
      && metrics.annotation_count > 0,
  )
}

function prodigyReadinessStatus(documentMeta: DocumentMeta | null, metrics: Metrics, labels: UiLabels['metrics']) {
  if (!documentMeta) return labels.noDocument
  if (annotationOverlapCount(metrics) > 0) return labels.exportOverlapConflict
  if (pendingSuggestionCount(metrics) > 0) return labels.exportNeedsReview
  if (metrics.completed_count < metrics.sentence_count) return labels.exportInProgress
  if (metrics.annotation_count === 0) return labels.exportNoSpans
  return labels.exportReady
}

function reviewOrder(recommendation: string) {
  return { accept: 0, reject: 1, uncertain: 2 }[recommendation as 'accept' | 'reject' | 'uncertain'] ?? 3
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

function queuePreviewText(item: ReviewQueueItem, labels: UiLabels['metrics'], order: ReviewQueueOrder) {
  const suggestion = item.first_suggestion
  const hint = item.action_hint || item.review_guidance?.action_hint || ''
  const candidateCount = item.candidate_suggestions?.length || item.review_guidance?.candidate_count || item.suggestion_count
  let score = `${Math.round(item.min_confidence * 100)}%`
  if (order === 'hybrid' && item.review_route === 'calibration') {
    score = `${labels.calibrationSample} ${Math.round(item.min_confidence * 100)}%`
  } else if (order === 'goldsmith' || order === 'hybrid') {
    score = `${labels.riskScore} ${item.risk_score.toFixed(2)}${riskBreakdownText(item, labels)}`
  }
  const candidate = suggestion ? `${suggestion.tag_name}: ${suggestion.text}` : labels.queuePreviewFallback(item.suggestion_count)
  const optionCount = candidateCount > 1 ? `${labels.candidateOptions(candidateCount)} · ` : ''
  return hint ? `${optionCount}${candidate} · ${hint} · ${score}` : `${optionCount}${candidate} · ${score}`
}

function riskBreakdownText(item: ReviewQueueItem, labels: UiLabels['metrics']) {
  const parts = [
    item.candidate_disagreement_score > 0 ? `${labels.candidateConflictRisk} ${item.candidate_disagreement_score.toFixed(2)}` : '',
    item.llm_review_risk_score > 0 ? `${labels.llmReviewRisk} ${item.llm_review_risk_score.toFixed(2)}` : '',
    item.judge_review_risk_score > 0 ? `${labels.judgeRisk} ${item.judge_review_risk_score.toFixed(2)}` : '',
    item.lexical_risk_score > 0 ? `${labels.lexicalRisk} ${item.lexical_risk_score.toFixed(2)}` : '',
  ].filter(Boolean)
  return parts.length ? ` · ${parts.join(' · ')}` : ''
}

function riskReasonLabels(item: ReviewQueueItem, labels: UiLabels['metrics']) {
  const reasonLabels = labels.riskReasonLabels as Record<string, string>
  return item.risk_reason_codes.slice(0, 4).map((code) => reasonLabels[code] ?? code)
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

    <div class="stats-quick-actions" :aria-label="labels.aria">
      <button class="export-button secondary compact" :disabled="!documentMeta" @click="emit('export-prodigy-bundle')">
        {{ labels.exportProdigyBundle }}
        <Download :size="17" aria-hidden="true" />
      </button>

      <button class="export-button secondary compact" :disabled="!documentMeta" @click="emit('export-prodigy')">
        {{ labels.exportProdigy }}
        <Download :size="17" aria-hidden="true" />
      </button>

      <button class="export-button secondary compact" :disabled="!documentMeta" @click="emit('export-prodigy-spans')">
        {{ labels.exportProdigySpans }}
        <Download :size="17" aria-hidden="true" />
      </button>

      <button class="export-button secondary compact" @click="emit('export-prodigy-labels')">
        {{ labels.exportProdigyLabels }}
        <Download :size="17" aria-hidden="true" />
      </button>

      <button class="export-button secondary compact" :disabled="!documentMeta || isSaving" @click="emit('auto-mark-monogloss')">
        {{ isSaving ? labels.markingMonogloss : labels.autoMarkMonogloss }}
        <Sparkles :size="17" aria-hidden="true" />
      </button>

      <button class="export-button danger compact reset-project-button" :disabled="isResetting" @click="emit('reset-project')">
        {{ isResetting ? labels.resetting : labels.resetProject }}
        <Trash2 :size="17" aria-hidden="true" />
      </button>
    </div>

    <article class="metric-card export-readiness-card" :class="{ warning: documentMeta && !isProdigyExportReady(documentMeta, metrics) }">
      <Download :size="22" aria-hidden="true" />
      <span>{{ labels.prodigyReadiness }}</span>
      <strong class="readiness-status">{{ prodigyReadinessStatus(documentMeta, metrics, labels) }}</strong>
      <div class="quality-list compact readiness-list" :aria-label="labels.prodigyReadiness">
        <div>
          <span>{{ labels.completedSentences }}</span>
          <strong>{{ labels.completedSummary(metrics.completed_count, metrics.sentence_count, progressPercent(metrics)) }}</strong>
        </div>
        <div>
          <span>{{ labels.labelCoverage }}</span>
          <strong>{{ labels.labelCoverageSummary(coveredLabelCount(metrics), totalLabelCount(metrics)) }}</strong>
        </div>
        <div>
          <span>{{ labels.pendingSuggestions }}</span>
          <strong>{{ pendingSuggestionCount(metrics) }}</strong>
        </div>
        <div>
          <span>{{ labels.overlappingSpans }}</span>
          <strong>{{ annotationOverlapCount(metrics) }}</strong>
        </div>
        <div>
          <span>{{ labels.exportFormat }}</span>
          <strong>Prodigy</strong>
        </div>
      </div>
      <div
        v-if="readinessActions(documentMeta, metrics, queueItems, reviewQueueDetails).length"
        class="readiness-action-list"
        :aria-label="labels.readinessActions"
      >
        <button
          v-for="action in readinessActions(documentMeta, metrics, queueItems, reviewQueueDetails)"
          :key="action.id"
          class="readiness-action-row"
          type="button"
          :disabled="readinessActionDisabled(action, isSaving)"
          @click="handleReadinessAction(action)"
        >
          <span>
            <strong>{{ readinessActionTitle(action, labels) }}</strong>
            <small>{{ readinessActionDetail(action, labels) }}</small>
          </span>
          <em>{{ readinessActionCta(action, labels) }}</em>
        </button>
      </div>
    </article>

    <section v-if="annotationConflictItems(queueItems).length" class="progress-card review-queue-card conflict-queue-card" :aria-label="labels.annotationConflicts">
      <div class="progress-header">
        <span>{{ labels.annotationConflicts }}</span>
        <strong>{{ annotationOverlapCount(metrics) }} {{ labels.conflicts }}</strong>
      </div>
      <div class="review-queue-list">
        <button
          v-for="item in annotationConflictItems(queueItems)"
          :key="item.id"
          class="review-queue-row"
          type="button"
          @click="emit('review-sentence', item.index)"
        >
          <span>
            <strong>#{{ item.index + 1 }} · {{ item.annotation_overlap_count }} {{ labels.conflicts }}</strong>
            <small>{{ labels.overlapConflictHint }}</small>
          </span>
          <em>{{ labels.go }}</em>
        </button>
      </div>
    </section>

    <div class="metric-stack">
      <article class="metric-card">
        <Target :size="22" aria-hidden="true" />
        <span>{{ labels.accuracy }}</span>
        <strong>{{ metrics.accuracy === null ? '--' : `${Math.round(metrics.accuracy * 100)}%` }}</strong>
        <small>
          {{ accuracyLabel(metrics.accuracy_label, labels) }}
          <template v-if="reviewSummary(metrics.suggestion_review_counts, labels)"> · {{ labels.reviewMix }} {{ reviewSummary(metrics.suggestion_review_counts, labels) }}</template>
          <template v-if="metrics.calibration_error_rate !== null">
            · {{ labels.calibrationError }} {{ Math.round(metrics.calibration_error_rate * 100) }}% ({{ metrics.calibration_disagreement_count }}/{{ metrics.calibration_count }})
          </template>
          <template v-if="efficiencySummary(metrics, labels)">
            · {{ efficiencySummary(metrics, labels) }}
          </template>
        </small>
      </article>
      <article class="metric-card">
        <MousePointer2 :size="22" aria-hidden="true" />
        <span>{{ labels.annotatedSpans }}</span>
        <strong>{{ metrics.annotation_count }}</strong>
        <small>{{ labels.persisted }}</small>
        <small v-if="metrics.annotation_count > 0 && metrics.annotation_label_counts.length" class="label-mix-heading">
          {{ labels.annotationLabelMix }}
        </small>
        <div v-if="metrics.annotation_count > 0 && metrics.annotation_label_counts.length" class="label-mix-list" :aria-label="labels.annotationLabelMix">
          <span
            v-for="item in visibleLabelCounts(metrics.annotation_label_counts)"
            :key="item.tag_id"
            class="label-mix-pill"
            :class="{ empty: item.count === 0 }"
            :style="{ '--label-color': item.color }"
            :title="labelMixTitle(item, labels)"
          >
            <em>{{ item.name }}</em>
            <strong>{{ item.count }}</strong>
          </span>
        </div>
      </article>
      <article class="metric-card">
        <Sparkles :size="22" aria-hidden="true" />
        <span>{{ labels.suggestions }}</span>
        <strong>{{ metrics.suggestion_count }}</strong>
        <small>
          {{ labels.ragQueue }}
          <template v-if="suggestionStatusSummary(metrics.suggestion_status_counts, labels)"> · {{ suggestionStatusSummary(metrics.suggestion_status_counts, labels) }}</template>
          <template v-if="sourceSummary(metrics.suggestion_source_counts, labels)"> · {{ labels.sourceMix }} {{ sourceSummary(metrics.suggestion_source_counts, labels) }}</template>
          <template v-if="confidenceSummary(metrics.suggestion_confidence_counts, labels)"> · {{ labels.confidenceMix }} {{ confidenceSummary(metrics.suggestion_confidence_counts, labels) }}</template>
        </small>
        <small v-if="metrics.suggestion_label_counts.length" class="label-mix-heading">
          {{ labels.suggestionLabelMix }}
        </small>
        <div v-if="metrics.suggestion_label_counts.length" class="label-mix-list compact" :aria-label="labels.suggestionLabelMix">
          <span
            v-for="item in visibleLabelCounts(metrics.suggestion_label_counts, 6)"
            :key="item.tag_id"
            class="label-mix-pill"
            :style="{ '--label-color': item.color }"
            :title="labelMixTitle(item, labels)"
          >
            <em>{{ item.name }}</em>
            <strong>{{ item.count }}</strong>
          </span>
        </div>
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
          <template v-if="runHistory[0] && runConfidenceSummary(runHistory[0], labels)"> · {{ labels.confidenceMix }} {{ runConfidenceSummary(runHistory[0], labels) }}</template>
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
        <span><kbd>S</kbd><em>{{ labels.selectSentence }}</em></span>
        <span><kbd>M</kbd><em>{{ labels.markMonogloss }}</em></span>
        <span><kbd>Enter</kbd><em>{{ labels.completeSentence }}</em></span>
        <span><kbd>Space / I</kbd><em>{{ labels.ignoreSentence }}</em></span>
        <span><kbd>J</kbd><em>{{ labels.rejectSentence }}</em></span>
        <span><kbd>E</kbd><em>{{ labels.reopenSentence }}</em></span>
        <span><kbd>Tab</kbd><em>{{ labels.cycleSuggestion }}</em></span>
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
              <template v-if="runConfidenceSummary(run, labels)"> · {{ labels.confidenceMix }} {{ runConfidenceSummary(run, labels) }}</template>
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
        <button type="button" :class="{ active: reviewQueueOrder === 'random' }" @click="emit('review-order-change', 'random')">
          {{ labels.random }}
        </button>
        <button type="button" :class="{ active: reviewQueueOrder === 'uncertain' }" @click="emit('review-order-change', 'uncertain')">
          {{ labels.uncertain }}
        </button>
        <button type="button" :class="{ active: reviewQueueOrder === 'goldsmith' }" @click="emit('review-order-change', 'goldsmith')">
          {{ labels.goldsmith }}
        </button>
        <button type="button" :class="{ active: reviewQueueOrder === 'hybrid' }" @click="emit('review-order-change', 'hybrid')">
          {{ labels.hybrid }}
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
            <small>{{ queuePreviewText(item, labels, reviewQueueOrder) }}</small>
            <span v-if="item.risk_reason_codes.length" class="review-risk-reasons" aria-hidden="true">
              <em v-for="reason in riskReasonLabels(item, labels)" :key="reason">{{ reason }}</em>
            </span>
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

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-bootstrap-report')">
      {{ labels.exportBootstrapReport }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-review-queue')">
      {{ labels.exportReviewQueue }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-human-choices')">
      {{ labels.exportHumanChoices }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-hard-examples')">
      {{ labels.exportHardExamples }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-boundary-feedback')">
      {{ labels.exportBoundaryFeedback }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-consistency-scores')">
      {{ labels.exportConsistencyScores }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-candidate-runs')">
      {{ labels.exportCandidateRuns }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-contrastive-examples')">
      {{ labels.exportContrastiveExamples }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-prompt-package')">
      {{ labels.exportPromptPackage }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-reflection-plans')">
      {{ labels.exportReflectionPlans }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-risk-reasons')">
      {{ labels.exportRiskReasons }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-label-statistics')">
      {{ labels.exportLabelStatistics }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-review-tasks')">
      {{ labels.exportReviewTasks }}
      <Download :size="18" aria-hidden="true" />
    </button>

    <button class="export-button secondary" :disabled="!documentMeta" @click="emit('export-goldsmith-verification-report')">
      {{ labels.exportVerificationReport }}
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
