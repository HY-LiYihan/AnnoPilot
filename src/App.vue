<script setup lang="ts">
import { Activity, Play, Plus, Settings } from '@lucide/vue'

const projectStats = [
  { label: 'Examples', value: '1,248', tone: 'warm' },
  { label: 'Review Queue', value: '86', tone: 'coral' },
  { label: 'Agreement', value: '92%', tone: 'sage' },
]

const workflow = [
  { name: 'Define', detail: 'Guidelines and labels', status: 'Ready' },
  { name: 'Calibrate', detail: 'Gold examples and prompt tests', status: '12 checks' },
  { name: 'Annotate', detail: 'Batch run with traces', status: 'Running' },
  { name: 'Review', detail: 'Resolve uncertain outputs', status: '86 left' },
]

const queueItems = [
  { title: 'Borderline metaphor usage', label: 'Needs review', confidence: '64%' },
  { title: 'Nested relation span', label: 'Conflict', confidence: '58%' },
  { title: 'Guideline exception case', label: 'Low confidence', confidence: '61%' },
]
</script>

<template>
  <main class="app-shell">
    <section class="hero" aria-labelledby="page-title">
      <nav class="topbar" aria-label="Primary">
        <div class="brand-mark" aria-hidden="true">A</div>
        <span class="brand-name">AnnoPilot</span>
        <button class="icon-button" aria-label="Open settings">
          <Settings :size="19" aria-hidden="true" />
        </button>
      </nav>

      <div class="hero-grid">
        <div class="hero-copy">
          <p class="eyebrow">Local-first annotation workbench</p>
          <h1 id="page-title">Turn evolving concepts into auditable datasets.</h1>
          <p class="lede">
            Calibrate guidelines, run assisted annotation, and review uncertain cases from a clean mobile-ready workspace.
          </p>
          <div class="hero-actions" aria-label="Project actions">
            <button class="primary-action">
              <Play :size="18" aria-hidden="true" />
              <span>Start review</span>
            </button>
            <button class="secondary-action">
              <Plus :size="18" aria-hidden="true" />
              <span>New project</span>
            </button>
          </div>
        </div>

        <aside class="run-card" aria-label="Current run status">
          <div class="run-card-header">
            <span class="run-title">
              <Activity :size="18" aria-hidden="true" />
              Batch Run
            </span>
            <strong>Live</strong>
          </div>
          <div class="progress-ring" aria-label="Run progress 71 percent">
            <span>71%</span>
          </div>
          <div class="run-meta">
            <span>2,930 traces written</span>
            <span>JSONL synced</span>
          </div>
        </aside>
      </div>
    </section>

    <section class="stats-grid" aria-label="Project summary">
      <article v-for="stat in projectStats" :key="stat.label" class="stat-card" :class="stat.tone">
        <span>{{ stat.label }}</span>
        <strong>{{ stat.value }}</strong>
      </article>
    </section>

    <section class="workspace-grid" aria-label="Workspace overview">
      <article class="panel workflow-panel">
        <div class="panel-heading">
          <div>
            <p class="section-kicker">Workflow</p>
            <h2>Project pipeline</h2>
          </div>
          <button class="text-button">View runs</button>
        </div>
        <ol class="workflow-list">
          <li v-for="step in workflow" :key="step.name">
            <div>
              <strong>{{ step.name }}</strong>
              <span>{{ step.detail }}</span>
            </div>
            <em>{{ step.status }}</em>
          </li>
        </ol>
      </article>

      <article class="panel review-panel">
        <div class="panel-heading compact">
          <div>
            <p class="section-kicker">Review</p>
            <h2>Next uncertain cases</h2>
          </div>
        </div>
        <div class="queue-list">
          <button v-for="item in queueItems" :key="item.title" class="queue-item">
            <span>
              <strong>{{ item.title }}</strong>
              <small>{{ item.label }}</small>
            </span>
            <em>{{ item.confidence }}</em>
          </button>
        </div>
      </article>
    </section>
  </main>
</template>
