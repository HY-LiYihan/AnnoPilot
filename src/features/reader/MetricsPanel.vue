<script setup lang="ts">
import { ArrowDownUp, BarChart3, Download, Gauge, MousePointer2, Target } from '@lucide/vue'
import type { DocumentMeta, Metrics } from '../../types/domain'

defineProps<{
  documentMeta: DocumentMeta | null
  metrics: Metrics
  progressPercent: number
  reviewedSummary: string
}>()

const emit = defineEmits<{
  export: []
}>()
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
        <strong>--</strong>
        <small>{{ metrics.accuracy_label }}</small>
      </article>
      <article class="metric-card">
        <MousePointer2 :size="22" aria-hidden="true" />
        <span>Annotated spans</span>
        <strong>{{ metrics.annotation_count }}</strong>
        <small>Persisted to SQLite</small>
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
          <span>Completed</span>
          <strong>{{ metrics.completed_count }}</strong>
        </div>
        <div>
          <span>Tokens</span>
          <strong>{{ documentMeta?.token_count ?? 0 }}</strong>
        </div>
        <div>
          <span>Keyboard</span>
          <strong><ArrowDownUp :size="17" aria-hidden="true" /> Enter</strong>
        </div>
      </div>
    </section>

    <button class="export-button" :disabled="!documentMeta" @click="emit('export')">
      Export JSONL
      <Download :size="18" aria-hidden="true" />
    </button>
  </aside>
</template>
