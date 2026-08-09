<script setup lang="ts">
import { ref } from 'vue'
import { AlertTriangle, Check, Pencil, Plus, Tag, Trash2, Upload, X } from '@lucide/vue'
import type { SentenceQueueItem, TagDef } from '../../types/domain'

defineProps<{
  tags: TagDef[]
  selectedTagId: string
  hasPendingSelection: boolean
  queueItems: SentenceQueueItem[]
  currentSentenceIndex: number
  reviewedSummary: string
  reviewSummary: string
  reviewQueueSummary: string
  isSaving: boolean
}>()

const emit = defineEmits<{
  'tag-click': [tagId: string]
  'tag-add': [name: string]
  'tag-rename': [tag: TagDef, name: string, description: string, examples: string[]]
  'tag-schema-import': [file: File]
  'tag-delete': [tag: TagDef]
  'sentence-click': [sentenceIndex: number]
}>()

const newTagName = ref('')
const editingTagId = ref('')
const editingTagName = ref('')
const editingTagDescription = ref('')
const editingTagExamples = ref('')
const pendingDeleteTag = ref<TagDef | null>(null)

function submitTag() {
  const name = newTagName.value.trim()
  if (!name) return
  emit('tag-add', name)
  newTagName.value = ''
}

function submitTagSchemaImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  emit('tag-schema-import', file)
  input.value = ''
}

function tagUsage(tag: TagDef) {
  return Math.max(tag.usage_count ?? 0, tag.count)
}

function tagSuggestionUsage(tag: TagDef) {
  return tag.suggestion_count ?? 0
}

function hasDeleteImpact(tag: TagDef) {
  return tagUsage(tag) > 0 || tagSuggestionUsage(tag) > 0
}

function deleteImpactSummary(tag: TagDef) {
  const impacts: string[] = []
  const annotationCount = tagUsage(tag)
  const suggestionCount = tagSuggestionUsage(tag)
  if (annotationCount > 0) impacts.push(`${annotationCount} 处标注`)
  if (suggestionCount > 0) impacts.push(`${suggestionCount} 条 AI 建议`)
  return impacts.join('，')
}

function deleteButtonLabel(tag: TagDef) {
  return hasDeleteImpact(tag) ? '删除标签和数据' : '删除标签'
}

function requestDeleteTag(tag: TagDef) {
  cancelEditTag()
  pendingDeleteTag.value = tag
}

function startEditTag(tag: TagDef) {
  pendingDeleteTag.value = null
  editingTagId.value = tag.id
  editingTagName.value = tag.name
  editingTagDescription.value = tag.description ?? ''
  editingTagExamples.value = tag.examples.join('，')
}

function cancelEditTag() {
  editingTagId.value = ''
  editingTagName.value = ''
  editingTagDescription.value = ''
  editingTagExamples.value = ''
}

function submitEditTag(tag: TagDef) {
  const name = editingTagName.value.trim()
  const description = editingTagDescription.value.trim()
  const normalizedDescription = description || null
  const examples = parseExamples(editingTagExamples.value)
  if (!name || (name === tag.name && normalizedDescription === (tag.description ?? null) && sameExamples(examples, tag.examples))) {
    cancelEditTag()
    return
  }
  emit('tag-rename', tag, name, description, examples)
  cancelEditTag()
}

function parseExamples(value: string) {
  const seen = new Set<string>()
  return value
    .split(/[，,、\s]+/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false
      seen.add(item)
      return true
    })
    .slice(0, 80)
}

function sameExamples(left: string[], right: string[]) {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function cancelDeleteTag() {
  pendingDeleteTag.value = null
}

function confirmDeleteTag() {
  if (!pendingDeleteTag.value) return
  emit('tag-delete', pendingDeleteTag.value)
  pendingDeleteTag.value = null
}
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

    <form class="tag-form" aria-label="Create annotation tag" @submit.prevent="submitTag">
      <input v-model="newTagName" type="text" maxlength="32" placeholder="新增标签" :disabled="isSaving" />
      <button type="submit" class="tag-add-button" :disabled="isSaving || !newTagName.trim()" title="新增标签">
        <Plus :size="16" aria-hidden="true" />
      </button>
    </form>

    <label class="tag-schema-import" title="导入 tag-schema.json">
      <Upload :size="15" aria-hidden="true" />
      <span>Import Tag Schema</span>
      <input type="file" accept="application/json,.json" :disabled="isSaving" @change="submitTagSchemaImport" />
    </label>

    <div class="tag-list" aria-label="Available annotation tags">
      <div
        v-for="tagItem in tags"
        :key="tagItem.id"
        class="tag-option"
        :class="{ selected: tagItem.id === selectedTagId, applyable: hasPendingSelection }"
        :style="{ '--tag-color': tagItem.color }"
      >
        <form v-if="editingTagId === tagItem.id" class="tag-edit-form" @submit.prevent="submitEditTag(tagItem)" @click.stop>
          <span class="tag-dot" aria-hidden="true"></span>
          <input v-model="editingTagName" type="text" maxlength="32" aria-label="重命名标签" :disabled="isSaving" />
          <input
            v-model="editingTagDescription"
            class="tag-guideline-input"
            type="text"
            maxlength="280"
            aria-label="标签准则说明"
            placeholder="标注准则说明"
            :disabled="isSaving"
          />
          <input
            v-model="editingTagExamples"
            class="tag-example-input"
            type="text"
            maxlength="800"
            aria-label="低算力 RAG 词面种子"
            placeholder="词面种子，用逗号或空格分隔"
            :disabled="isSaving"
          />
          <button type="submit" class="tag-edit-button" :disabled="isSaving || !editingTagName.trim()" title="保存标签名">
            <Check :size="15" aria-hidden="true" />
          </button>
          <button type="button" class="tag-edit-button" :disabled="isSaving" title="取消重命名" @click="cancelEditTag">
            <X :size="15" aria-hidden="true" />
          </button>
        </form>
        <button v-else type="button" class="tag-main-button" @click="emit('tag-click', tagItem.id)">
          <span class="tag-dot" aria-hidden="true"></span>
          <span class="tag-copy">
            <strong>{{ tagItem.name }}</strong>
            <small>{{ tagUsage(tagItem) }} 处标注</small>
            <em v-if="tagItem.description">{{ tagItem.description }}</em>
            <em v-if="tagItem.examples.length">{{ tagItem.examples.length }} 个 RAG 词面种子</em>
          </span>
          <kbd>{{ tagItem.shortcut }}</kbd>
        </button>
        <button
          v-if="editingTagId !== tagItem.id"
          type="button"
          class="tag-edit-button"
          :disabled="isSaving"
          title="重命名标签"
          @click.stop="startEditTag(tagItem)"
        >
          <Pencil :size="15" aria-hidden="true" />
        </button>
        <button
          v-if="editingTagId !== tagItem.id"
          type="button"
          class="tag-delete-button"
          :disabled="isSaving || tags.length <= 1"
          :title="
            tags.length <= 1
              ? '至少保留一个标签'
              : hasDeleteImpact(tagItem)
                ? `删除标签，并删除 ${deleteImpactSummary(tagItem)}`
                : '删除标签'
          "
          @click="requestDeleteTag(tagItem)"
        >
          <Trash2 :size="15" aria-hidden="true" />
        </button>
      </div>
    </div>

    <div v-if="pendingDeleteTag" class="tag-delete-confirm" role="alertdialog" aria-live="polite">
      <button type="button" class="confirm-close" aria-label="取消删除" @click="cancelDeleteTag">
        <X :size="15" aria-hidden="true" />
      </button>
      <div class="confirm-icon" aria-hidden="true">
        <AlertTriangle :size="18" />
      </div>
      <div class="confirm-copy">
        <strong>删除「{{ pendingDeleteTag.name }}」？</strong>
        <p v-if="tagUsage(pendingDeleteTag) > 0">
          这个标签已用于 {{ tagUsage(pendingDeleteTag) }} 处标注；确认删除后，所有使用「{{ pendingDeleteTag.name }}」的标注数据都会一并删除。
        </p>
        <p v-else>这个标签还没有被用于人工标注，删除后只会从标签列表移除。</p>
        <p v-if="tagSuggestionUsage(pendingDeleteTag) > 0">同时会删除 {{ tagSuggestionUsage(pendingDeleteTag) }} 条对应的 AI 建议。</p>
      </div>
      <div class="confirm-actions">
        <button type="button" class="ghost-button" :disabled="isSaving" @click="cancelDeleteTag">取消</button>
        <button type="button" class="danger-button" :disabled="isSaving" @click="confirmDeleteTag">
          {{ deleteButtonLabel(pendingDeleteTag) }}
        </button>
      </div>
    </div>

    <div class="queue-block" aria-label="Sentence progress grid">
      <div class="mini-heading">
        <span>句子进度</span>
        <em>{{ reviewedSummary }} · {{ reviewSummary }} · {{ reviewQueueSummary }}</em>
      </div>
      <div class="sentence-dot-grid">
        <button
          v-for="sentence in queueItems"
          :key="sentence.id"
          class="sentence-dot"
          :class="{
            active: sentence.index === currentSentenceIndex,
            completed: sentence.completed,
            rejected: sentence.answer === 'reject',
            ignored: sentence.answer === 'ignore',
            'needs-review': !sentence.completed && sentence.suggestion_count > 0,
            untouched: !sentence.completed && !sentence.suggestion_count,
          }"
          :aria-label="`Sentence ${sentence.index + 1}: ${sentence.answer === 'reject' ? 'rejected' : sentence.answer === 'ignore' ? 'ignored' : sentence.completed ? 'completed' : sentence.suggestion_count ? 'needs review' : 'untouched'}`"
          :title="`#${sentence.index + 1} ${sentence.answer === 'reject' ? 'Rejected' : sentence.answer === 'ignore' ? 'Ignored' : sentence.completed ? 'Completed' : sentence.suggestion_count ? 'Needs review' : 'Untouched'}`"
          @click="emit('sentence-click', sentence.index)"
        >
          <span>{{ sentence.index + 1 }}</span>
        </button>
      </div>
      <div class="sentence-dot-legend" aria-label="Sentence status legend">
        <span><i class="legend-dot completed"></i>已标完</span>
        <span><i class="legend-dot rejected"></i>已拒绝</span>
        <span><i class="legend-dot ignored"></i>已忽略</span>
        <span><i class="legend-dot needs-review"></i>待确认</span>
        <span><i class="legend-dot untouched"></i>未开始</span>
      </div>
    </div>
  </aside>
</template>
