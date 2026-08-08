<script setup lang="ts">
import {
  Activity,
  BarChart3,
  CheckCircle,
  ChevronRight,
  Clock,
  Gauge,
  Settings,
  Tag,
  Target,
} from '@lucide/vue'

const annotationTags = [
  { name: 'Environmental Impact', shortcut: '1', color: '#0b7565', count: 18, active: true },
  { name: 'Action', shortcut: '2', color: '#326bd8', count: 24, active: false },
  { name: 'Target', shortcut: '3', color: '#c45a2e', count: 11, active: false },
  { name: 'Organization', shortcut: '4', color: '#7a3db8', count: 9, active: false },
  { name: 'Evidence', shortcut: '5', color: '#b98600', count: 15, active: false },
  { name: 'Risk Signal', shortcut: '6', color: '#b43b59', count: 6, active: false },
]

const annotationQueue = [
  { id: 'DOC-001', status: 'In progress', tone: 'active' },
  { id: 'DOC-002', status: 'Pending', tone: 'pending' },
  { id: 'DOC-003', status: 'Pending', tone: 'pending' },
  { id: 'DOC-004', status: 'Empty', tone: 'empty' },
]

const metrics = [
  { label: 'Progress', value: '68%', detail: '842 / 1,248 examples', icon: Gauge },
  { label: 'Accuracy', value: '92.4%', detail: '+3.1 pts vs last run', icon: Target },
  { label: 'Agreement', value: '88%', detail: 'human and model match', icon: CheckCircle },
]

const activeSpans = [
  { label: 'carbon emissions', tag: 'Environmental Impact', confidence: '94%' },
  { label: 'reduce', tag: 'Action', confidence: '87%' },
  { label: '50 percent by 2030', tag: 'Target', confidence: '76%' },
]
</script>

<template>
  <main class="app-shell">
    <nav class="topbar" aria-label="Primary">
      <div class="brand-cluster">
        <div class="brand-mark" aria-hidden="true">A</div>
        <div>
          <span class="brand-name">AnnoPilot</span>
          <small>Annotation Workbench</small>
        </div>
      </div>

      <div class="run-status" aria-label="Current run status">
        <Activity :size="17" aria-hidden="true" />
        <span>Batch run live</span>
      </div>

      <button class="icon-button" aria-label="Open settings">
        <Settings :size="19" aria-hidden="true" />
      </button>
    </nav>

    <section class="workbench" aria-label="Annotation workspace">
      <aside class="side-panel tag-panel" aria-labelledby="tag-panel-title">
        <div class="panel-heading">
          <div>
            <p class="section-kicker">Tags</p>
            <h2 id="tag-panel-title">Label palette</h2>
          </div>
          <Tag :size="20" aria-hidden="true" />
        </div>

        <div class="tag-list" aria-label="Available annotation tags">
          <button
            v-for="tagItem in annotationTags"
            :key="tagItem.name"
            class="tag-option"
            :class="{ selected: tagItem.active }"
            :style="{ '--tag-color': tagItem.color }"
          >
            <span class="tag-dot" aria-hidden="true"></span>
            <span class="tag-copy">
              <strong>{{ tagItem.name }}</strong>
              <small>{{ tagItem.count }} spans</small>
            </span>
            <kbd>{{ tagItem.shortcut }}</kbd>
          </button>
        </div>

        <div class="queue-block" aria-label="Corpus queue">
          <div class="mini-heading">
            <span>Corpus queue</span>
            <em>42 left</em>
          </div>
          <button v-for="item in annotationQueue" :key="item.id" class="queue-row" :class="item.tone">
            <span>{{ item.id }}</span>
            <strong>{{ item.status }}</strong>
          </button>
        </div>
      </aside>

      <section class="editor-panel" aria-labelledby="editor-title">
        <div class="editor-header">
          <div>
            <p class="section-kicker">Annotation Editor</p>
            <h1 id="editor-title">Climate policy extraction</h1>
          </div>
          <div class="document-pill">
            <Clock :size="16" aria-hidden="true" />
            <span>DOC-001</span>
          </div>
        </div>

        <article class="text-surface" aria-label="Text needing annotation">
          <p>
            The company announced a new plan to
            <mark class="span-action">reduce</mark>
            <mark class="span-impact">carbon emissions</mark>
            by <mark class="span-target">50 percent by the year 2030</mark> across all global operations.
            The disclosure cites verified energy procurement records and supplier audits as supporting evidence.
            Analysts flagged the transition plan as credible but dependent on regional policy incentives.
          </p>
        </article>

        <section class="candidate-card" aria-labelledby="candidate-title">
          <div class="candidate-heading">
            <h2 id="candidate-title">Detected candidates</h2>
            <span>3 selected</span>
          </div>
          <div class="candidate-list">
            <button v-for="span in activeSpans" :key="span.label" class="candidate-row">
              <span>
                <strong>{{ span.label }}</strong>
                <small>{{ span.tag }}</small>
              </span>
              <em>{{ span.confidence }}</em>
            </button>
          </div>
        </section>

        <section class="verification-card" aria-label="Human verification actions">
          <div>
            <p class="section-kicker">Human Verification</p>
            <h2>Confirm current spans</h2>
          </div>
          <div class="verification-actions">
            <button class="accept-button">Accept</button>
            <button class="edit-button">Edit span</button>
            <button class="skip-button">Skip</button>
          </div>
        </section>
      </section>

      <aside class="side-panel stats-panel" aria-labelledby="stats-panel-title">
        <div class="panel-heading">
          <div>
            <p class="section-kicker">Evidence</p>
            <h2 id="stats-panel-title">Run metrics</h2>
          </div>
          <BarChart3 :size="21" aria-hidden="true" />
        </div>

        <div class="metric-stack">
          <article v-for="metric in metrics" :key="metric.label" class="metric-card">
            <component :is="metric.icon" :size="22" aria-hidden="true" />
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
            <small>{{ metric.detail }}</small>
          </article>
        </div>

        <section class="progress-card" aria-label="Annotation progress">
          <div class="progress-header">
            <span>Today</span>
            <strong>126 reviewed</strong>
          </div>
          <div class="progress-track" aria-hidden="true">
            <span></span>
          </div>
          <div class="quality-list">
            <div>
              <span>High confidence</span>
              <strong>74%</strong>
            </div>
            <div>
              <span>Needs review</span>
              <strong>18%</strong>
            </div>
            <div>
              <span>Rejected</span>
              <strong>8%</strong>
            </div>
          </div>
        </section>

        <button class="export-button">
          Export JSONL
          <ChevronRight :size="18" aria-hidden="true" />
        </button>
      </aside>
    </section>
  </main>
</template>
