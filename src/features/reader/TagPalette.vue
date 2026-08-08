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
        <p class="section-kicker">Tags</p>
        <h2 id="tag-panel-title">Label palette</h2>
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
          <small>{{ tagItem.count }} spans</small>
        </span>
        <kbd>{{ tagItem.shortcut }}</kbd>
      </button>
    </div>

    <div class="queue-block" aria-label="Corpus queue">
      <div class="mini-heading">
        <span>Sentences</span>
        <em>{{ reviewedSummary }}</em>
      </div>
      <button
        v-for="sentence in queueItems"
        :key="sentence.id"
        class="queue-row"
        :class="{
          active: sentence.index === currentSentenceIndex,
          completed: sentence.completed,
          pending: !sentence.completed,
        }"
        @click="emit('sentence-click', sentence.index)"
      >
        <span>#{{ sentence.index + 1 }}</span>
        <strong>{{ sentence.completed ? 'Done' : sentence.index === currentSentenceIndex ? 'Active' : 'Pending' }}</strong>
      </button>
    </div>
  </aside>
</template>
