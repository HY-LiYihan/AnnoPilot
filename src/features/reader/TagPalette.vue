<script setup lang="ts">
import { Tag } from '@lucide/vue'
import type { SentenceDef, TagDef } from '../../types/domain'

defineProps<{
  tags: TagDef[]
  selectedTagId: string
  hasPendingSelection: boolean
  queueItems: SentenceDef[]
  currentSentenceIndex: number
  reviewedSummary: string
}>()

const emit = defineEmits<{
  'tag-click': [tagId: string]
  'sentence-click': [sentenceIndex: number]
}>()
</script>

<template>
  <aside class="side-panel tag-panel" aria-labelledby="tag-panel-title">
    <div class="panel-heading">
      <div>
        <p class="section-kicker">POS Tags</p>
        <h2 id="tag-panel-title">词性标签</h2>
      </div>
      <Tag :size="20" aria-hidden="true" />
    </div>

    <div class="tag-list" aria-label="Available annotation tags">
      <button
        v-for="tagItem in tags"
        :key="tagItem.id"
        class="tag-option"
        :class="{ selected: tagItem.id === selectedTagId, applyable: hasPendingSelection }"
        :style="{ '--tag-color': tagItem.color }"
        @click="emit('tag-click', tagItem.id)"
      >
        <span class="tag-dot" aria-hidden="true"></span>
        <span class="tag-copy">
          <strong>{{ tagItem.name }}</strong>
          <small>{{ tagItem.count }} 处标注</small>
        </span>
        <kbd>{{ tagItem.shortcut }}</kbd>
      </button>
    </div>

    <div class="queue-block" aria-label="Sentence progress grid">
      <div class="mini-heading">
        <span>句子进度</span>
        <em>{{ reviewedSummary }}</em>
      </div>
      <div class="sentence-dot-grid">
        <button
          v-for="sentence in queueItems"
          :key="sentence.id"
          class="sentence-dot"
          :class="{
            active: sentence.index === currentSentenceIndex,
            completed: sentence.completed,
            untouched: !sentence.completed,
          }"
          :aria-label="`Sentence ${sentence.index + 1}: ${sentence.completed ? 'completed' : 'untouched'}`"
          :title="`#${sentence.index + 1} ${sentence.completed ? 'Completed' : 'Untouched'}`"
          @click="emit('sentence-click', sentence.index)"
        >
          <span>{{ sentence.index + 1 }}</span>
        </button>
      </div>
      <div class="sentence-dot-legend" aria-label="Sentence status legend">
        <span><i class="legend-dot completed"></i>已标完</span>
        <span><i class="legend-dot needs-review"></i>待确认</span>
        <span><i class="legend-dot untouched"></i>未开始</span>
      </div>
    </div>
  </aside>
</template>
