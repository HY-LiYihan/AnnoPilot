<script setup lang="ts">
import { ref } from 'vue'
import { AlertTriangle, Check, Pencil, Plus, Tag, Trash2, Upload, X } from '@lucide/vue'
import type { UiLabels } from '../../i18n'
import type { SentenceQueueItem, TagDef } from '../../types/domain'

defineProps<{
  labels: UiLabels['tags']
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

function deleteImpactSummary(tag: TagDef, labels: UiLabels['tags']) {
  const impacts: string[] = []
  const annotationCount = tagUsage(tag)
  const suggestionCount = tagSuggestionUsage(tag)
  if (annotationCount > 0) impacts.push(labels.annotationCount(annotationCount))
  if (suggestionCount > 0) impacts.push(labels.suggestionCount(suggestionCount))
  return impacts.join('，')
}

function deleteButtonLabel(tag: TagDef, labels: UiLabels['tags']) {
  return hasDeleteImpact(tag) ? labels.deleteButtonWithData : labels.deleteButton
}

function sentenceStatusLabel(sentence: SentenceQueueItem, labels: UiLabels['tags']) {
  if (sentence.answer === 'reject') return labels.rejected
  if (sentence.answer === 'ignore') return labels.ignored
  if (sentence.completed) return labels.completed
  if (sentence.suggestion_count) return labels.needsReview
  return labels.untouched
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
  <aside class="side-panel tag-panel" aria-labelledby="tag-panel-title" :aria-label="labels.aria">
    <div class="panel-heading">
      <div>
        <p class="section-kicker">{{ labels.kicker }}</p>
        <h2 id="tag-panel-title">{{ labels.title }}</h2>
      </div>
      <Tag :size="20" aria-hidden="true" />
    </div>

    <form class="tag-form" :aria-label="labels.createAria" @submit.prevent="submitTag">
      <input v-model="newTagName" type="text" maxlength="32" :placeholder="labels.addPlaceholder" :disabled="isSaving" />
      <button type="submit" class="tag-add-button" :disabled="isSaving || !newTagName.trim()" :title="labels.addTitle">
        <Plus :size="16" aria-hidden="true" />
      </button>
    </form>

    <label class="tag-schema-import" :title="labels.importSchema">
      <Upload :size="15" aria-hidden="true" />
      <span>{{ labels.importSchema }}</span>
      <input type="file" accept="application/json,.json" :disabled="isSaving" @change="submitTagSchemaImport" />
    </label>

    <div class="tag-list" :aria-label="labels.availableAria">
      <div
        v-for="tagItem in tags"
        :key="tagItem.id"
        class="tag-option"
        :class="{ selected: tagItem.id === selectedTagId, applyable: hasPendingSelection }"
        :style="{ '--tag-color': tagItem.color }"
      >
        <form v-if="editingTagId === tagItem.id" class="tag-edit-form" @submit.prevent="submitEditTag(tagItem)" @click.stop>
          <span class="tag-dot" aria-hidden="true"></span>
          <input v-model="editingTagName" type="text" maxlength="32" :aria-label="labels.renameAria" :disabled="isSaving" />
          <input
            v-model="editingTagDescription"
            class="tag-guideline-input"
            type="text"
            maxlength="280"
            :aria-label="labels.guidelineAria"
            :placeholder="labels.guidelinePlaceholder"
            :disabled="isSaving"
          />
          <input
            v-model="editingTagExamples"
            class="tag-example-input"
            type="text"
            maxlength="800"
            :aria-label="labels.examplesAria"
            :placeholder="labels.examplesPlaceholder"
            :disabled="isSaving"
          />
          <button type="submit" class="tag-edit-button" :disabled="isSaving || !editingTagName.trim()" :title="labels.saveTitle">
            <Check :size="15" aria-hidden="true" />
          </button>
          <button type="button" class="tag-edit-button" :disabled="isSaving" :title="labels.cancelTitle" @click="cancelEditTag">
            <X :size="15" aria-hidden="true" />
          </button>
        </form>
        <button v-else type="button" class="tag-main-button" @click="emit('tag-click', tagItem.id)">
          <span class="tag-dot" aria-hidden="true"></span>
          <span class="tag-copy">
            <strong>{{ tagItem.name }}</strong>
            <small>{{ labels.annotationCount(tagUsage(tagItem)) }}</small>
            <em v-if="tagItem.description">{{ tagItem.description }}</em>
            <em v-if="tagItem.examples.length">{{ labels.ragSeedCount(tagItem.examples.length) }}</em>
          </span>
          <kbd>{{ tagItem.shortcut }}</kbd>
        </button>
        <button
          v-if="editingTagId !== tagItem.id"
          type="button"
          class="tag-edit-button"
          :disabled="isSaving"
          :title="labels.editTitle"
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
              ? labels.keepOneTitle
              : hasDeleteImpact(tagItem)
                ? labels.deleteWithDataTitle(deleteImpactSummary(tagItem, labels))
                : labels.deleteTitle
          "
          @click="requestDeleteTag(tagItem)"
        >
          <Trash2 :size="15" aria-hidden="true" />
        </button>
      </div>
    </div>

    <div v-if="pendingDeleteTag" class="tag-delete-confirm" role="alertdialog" aria-live="polite">
      <button type="button" class="confirm-close" :aria-label="labels.cancelTitle" @click="cancelDeleteTag">
        <X :size="15" aria-hidden="true" />
      </button>
      <div class="confirm-icon" aria-hidden="true">
        <AlertTriangle :size="18" />
      </div>
      <div class="confirm-copy">
        <strong>{{ labels.deleteQuestion(pendingDeleteTag.name) }}</strong>
        <p v-if="tagUsage(pendingDeleteTag) > 0">
          {{ labels.deleteAnnotationWarning(pendingDeleteTag.name, tagUsage(pendingDeleteTag)) }}
        </p>
        <p v-else>{{ labels.deleteUnused }}</p>
        <p v-if="tagSuggestionUsage(pendingDeleteTag) > 0">{{ labels.deleteSuggestionWarning(tagSuggestionUsage(pendingDeleteTag)) }}</p>
      </div>
      <div class="confirm-actions">
        <button type="button" class="ghost-button" :disabled="isSaving" @click="cancelDeleteTag">{{ labels.cancel }}</button>
        <button type="button" class="danger-button" :disabled="isSaving" @click="confirmDeleteTag">
          {{ deleteButtonLabel(pendingDeleteTag, labels) }}
        </button>
      </div>
    </div>

    <div class="queue-block" :aria-label="labels.progressAria">
      <div class="mini-heading">
        <span>{{ labels.progressTitle }}</span>
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
          :aria-label="labels.sentenceAria(sentence.index + 1, sentenceStatusLabel(sentence, labels))"
          :title="labels.sentenceTitle(sentence.index + 1, sentenceStatusLabel(sentence, labels))"
          @click="emit('sentence-click', sentence.index)"
        >
          <span>{{ sentence.index + 1 }}</span>
        </button>
      </div>
      <div class="sentence-dot-legend" :aria-label="labels.legendAria">
        <span><i class="legend-dot completed"></i>{{ labels.completed }}</span>
        <span><i class="legend-dot rejected"></i>{{ labels.rejected }}</span>
        <span><i class="legend-dot ignored"></i>{{ labels.ignored }}</span>
        <span><i class="legend-dot needs-review"></i>{{ labels.needsReview }}</span>
        <span><i class="legend-dot untouched"></i>{{ labels.untouched }}</span>
      </div>
    </div>
  </aside>
</template>
