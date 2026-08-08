<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArrowDownUp,
  BarChart3,
  CheckCircle,
  Clock,
  Download,
  FileText,
  Gauge,
  MousePointer2,
  Settings,
  Tag,
  Target,
  Upload,
} from '@lucide/vue'

const PROJECT_ID = 'default'
const ACTIVE_DOCUMENT_KEY = 'annopilot.activeDocumentId'

type TagDef = {
  id: string
  name: string
  shortcut: string
  color: string
  count: number
}

type TokenDef = {
  id: string
  token_index: number
  text: string
  start_char: number
  end_char: number
}

type AnnotationDef = {
  id: string
  tag_id: string
  tag_name: string
  tag_color: string
  start_token_index: number
  end_token_index: number
  start_char: number
  end_char: number
  text: string
  created_at: string
}

type SentenceDef = {
  id: string
  index: number
  text: string
  start_char: number
  end_char: number
  completed: boolean
  tokens: TokenDef[]
  annotations: AnnotationDef[]
}

type DocumentMeta = {
  id: string
  filename: string
  sentence_count: number
  token_count: number
}

type Metrics = {
  sentence_count: number
  completed_count: number
  progress: number
  annotation_count: number
  accuracy: number | null
  accuracy_label: string
}

type DocumentPayload = {
  document: DocumentMeta
  tags: TagDef[]
  sentences: SentenceDef[]
  metrics: Metrics
}

type DragSelection = {
  sentenceId: string
  start: number
  end: number
}

const fallbackTags: TagDef[] = [
  { id: 'environmental_impact', name: 'Environmental Impact', shortcut: '1', color: '#0b7565', count: 0 },
  { id: 'action', name: 'Action', shortcut: '2', color: '#326bd8', count: 0 },
  { id: 'target', name: 'Target', shortcut: '3', color: '#c45a2e', count: 0 },
  { id: 'organization', name: 'Organization', shortcut: '4', color: '#7a3db8', count: 0 },
  { id: 'evidence', name: 'Evidence', shortcut: '5', color: '#b98600', count: 0 },
  { id: 'risk_signal', name: 'Risk Signal', shortcut: '6', color: '#b43b59', count: 0 },
]

const tags = ref<TagDef[]>(fallbackTags)
const documentMeta = ref<DocumentMeta | null>(null)
const sentences = ref<SentenceDef[]>([])
const metrics = ref<Metrics>({
  sentence_count: 0,
  completed_count: 0,
  progress: 0,
  annotation_count: 0,
  accuracy: null,
  accuracy_label: 'Waiting for review data',
})
const selectedTagId = ref(fallbackTags[0].id)
const currentSentenceIndex = ref(0)
const dragSelection = ref<DragSelection | null>(null)
const isUploading = ref(false)
const isSaving = ref(false)
const readerError = ref('')
const sentenceElements = ref<Record<string, HTMLElement | null>>({})

const currentSentence = computed(() => sentences.value[currentSentenceIndex.value] ?? null)
const selectedTag = computed(() => tags.value.find((tagItem) => tagItem.id === selectedTagId.value) ?? tags.value[0])
const progressPercent = computed(() => Math.round(metrics.value.progress * 100))
const reviewedSummary = computed(() => `${metrics.value.completed_count} / ${metrics.value.sentence_count || 0}`)
const activeAnnotations = computed(() => currentSentence.value?.annotations ?? [])
const queueItems = computed(() => sentences.value.slice(0, 8))

onMounted(async () => {
  window.addEventListener('keydown', handleKeydown)
  const activeDocumentId = window.localStorage.getItem(ACTIVE_DOCUMENT_KEY)
  if (activeDocumentId) {
    await loadDocument(activeDocumentId)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
})

async function handleImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  readerError.value = ''
  isUploading.value = true
  const formData = new FormData()
  formData.append('file', file)

  try {
    const response = await fetch(`/api/projects/${PROJECT_ID}/import-txt`, {
      method: 'POST',
      body: formData,
    })
    if (!response.ok) throw new Error(await responseMessage(response))
    const imported = await response.json()
    tags.value = imported.tags
    selectedTagId.value = imported.tags[0]?.id ?? selectedTagId.value
    window.localStorage.setItem(ACTIVE_DOCUMENT_KEY, imported.document_id)
    await loadDocument(imported.document_id)
  } catch (error) {
    readerError.value = error instanceof Error ? error.message : 'Import failed.'
  } finally {
    isUploading.value = false
    input.value = ''
  }
}

async function loadDocument(documentId: string, preserveCurrent = false) {
  try {
    const previousIndex = currentSentenceIndex.value
    const response = await fetch(`/api/projects/${PROJECT_ID}/documents/${documentId}`)
    if (!response.ok) throw new Error(await responseMessage(response))
    const payload = (await response.json()) as DocumentPayload
    documentMeta.value = payload.document
    sentences.value = payload.sentences
    tags.value = payload.tags.length ? payload.tags : fallbackTags
    selectedTagId.value = tags.value.find((tagItem) => tagItem.id === selectedTagId.value)?.id ?? tags.value[0].id
    metrics.value = payload.metrics
    currentSentenceIndex.value = preserveCurrent
      ? clampIndex(previousIndex)
      : Math.max(
          0,
          payload.sentences.findIndex((sentence) => !sentence.completed),
        )
    if (currentSentenceIndex.value < 0) currentSentenceIndex.value = 0
    await centerCurrentSentence()
  } catch (error) {
    window.localStorage.removeItem(ACTIVE_DOCUMENT_KEY)
    readerError.value = error instanceof Error ? error.message : 'Could not load document.'
  }
}

function setCurrentSentence(index: number) {
  if (!sentences.value.length) return
  currentSentenceIndex.value = clampIndex(index)
  void centerCurrentSentence()
}

async function completeCurrentSentence() {
  const sentence = currentSentence.value
  if (!sentence || isSaving.value) return
  isSaving.value = true
  readerError.value = ''
  try {
    const response = await fetch(`/api/projects/${PROJECT_ID}/sentences/${sentence.id}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ completed: true }),
    })
    if (!response.ok) throw new Error(await responseMessage(response))
    sentence.completed = true
    recomputeLocalMetrics()
    setCurrentSentence(Math.min(currentSentenceIndex.value + 1, sentences.value.length - 1))
  } catch (error) {
    readerError.value = error instanceof Error ? error.message : 'Could not complete sentence.'
  } finally {
    isSaving.value = false
  }
}

function handleKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  if (target?.matches('input, textarea, select')) return
  if (event.key === 'Enter') {
    event.preventDefault()
    void completeCurrentSentence()
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    setCurrentSentence(currentSentenceIndex.value + 1)
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    setCurrentSentence(currentSentenceIndex.value - 1)
  }
}

function setSentenceElement(sentenceId: string, element: unknown) {
  sentenceElements.value[sentenceId] = element as HTMLElement | null
}

async function centerCurrentSentence() {
  await nextTick()
  const sentence = currentSentence.value
  if (!sentence) return
  sentenceElements.value[sentence.id]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

function onSentenceClick(sentenceIndex: number) {
  if (sentenceIndex !== currentSentenceIndex.value) setCurrentSentence(sentenceIndex)
}

function onTokenPointerDown(sentence: SentenceDef, tokenIndex: number, event: PointerEvent) {
  if (sentence.index !== currentSentenceIndex.value) {
    setCurrentSentence(sentence.index)
    return
  }
  event.preventDefault()
  const existing = annotationForToken(sentence, tokenIndex)
  if (existing) {
    void deleteAnnotation(existing.id)
    return
  }
  dragSelection.value = { sentenceId: sentence.id, start: tokenIndex, end: tokenIndex }
}

function onTokenPointerEnter(sentence: SentenceDef, tokenIndex: number) {
  if (dragSelection.value?.sentenceId !== sentence.id) return
  dragSelection.value = { ...dragSelection.value, end: tokenIndex }
}

function onTokenPointerUp(sentence: SentenceDef, tokenIndex: number) {
  const selection = dragSelection.value
  if (!selection || selection.sentenceId !== sentence.id) return
  dragSelection.value = null
  void createAnnotation(sentence, selection.start, tokenIndex)
}

async function createAnnotation(sentence: SentenceDef, start: number, end: number) {
  const tag = selectedTag.value
  if (!tag || isSaving.value) return
  isSaving.value = true
  readerError.value = ''
  const [startTokenIndex, endTokenIndex] = [Math.min(start, end), Math.max(start, end)]
  try {
    const response = await fetch(`/api/projects/${PROJECT_ID}/sentences/${sentence.id}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tag_id: tag.id,
        start_token_index: startTokenIndex,
        end_token_index: endTokenIndex,
      }),
    })
    if (!response.ok) throw new Error(await responseMessage(response))
    const payload = await response.json()
    replaceSentenceAnnotations(sentence.id, payload.annotations)
  } catch (error) {
    readerError.value = error instanceof Error ? error.message : 'Could not save annotation.'
  } finally {
    isSaving.value = false
  }
}

async function deleteAnnotation(annotationId: string) {
  if (isSaving.value) return
  isSaving.value = true
  readerError.value = ''
  try {
    const response = await fetch(`/api/projects/${PROJECT_ID}/annotations/${annotationId}`, { method: 'DELETE' })
    if (!response.ok) throw new Error(await responseMessage(response))
    sentences.value = sentences.value.map((sentence) => ({
      ...sentence,
      annotations: sentence.annotations.filter((annotation) => annotation.id !== annotationId),
    }))
    recomputeLocalMetrics()
  } catch (error) {
    readerError.value = error instanceof Error ? error.message : 'Could not delete annotation.'
  } finally {
    isSaving.value = false
  }
}

function replaceSentenceAnnotations(sentenceId: string, annotations: AnnotationDef[]) {
  sentences.value = sentences.value.map((sentence) =>
    sentence.id === sentenceId ? { ...sentence, annotations } : sentence,
  )
  recomputeLocalMetrics()
}

function recomputeLocalMetrics() {
  const completedCount = sentences.value.filter((sentence) => sentence.completed).length
  const annotationCount = sentences.value.reduce((total, sentence) => total + sentence.annotations.length, 0)
  const counts = new Map<string, number>()
  for (const sentence of sentences.value) {
    for (const annotation of sentence.annotations) {
      counts.set(annotation.tag_id, (counts.get(annotation.tag_id) ?? 0) + 1)
    }
  }
  tags.value = tags.value.map((tagItem) => ({ ...tagItem, count: counts.get(tagItem.id) ?? 0 }))
  metrics.value = {
    ...metrics.value,
    sentence_count: sentences.value.length,
    completed_count: completedCount,
    progress: sentences.value.length ? completedCount / sentences.value.length : 0,
    annotation_count: annotationCount,
  }
}

function annotationForToken(sentence: SentenceDef, tokenIndex: number) {
  return sentence.annotations.find(
    (annotation) => annotation.start_token_index <= tokenIndex && annotation.end_token_index >= tokenIndex,
  )
}

function isTokenInDrag(sentence: SentenceDef, tokenIndex: number) {
  const selection = dragSelection.value
  if (!selection || selection.sentenceId !== sentence.id) return false
  return tokenIndex >= Math.min(selection.start, selection.end) && tokenIndex <= Math.max(selection.start, selection.end)
}

function tokenPrefix(sentence: SentenceDef, tokenIndex: number) {
  const token = sentence.tokens[tokenIndex]
  const previousEnd = tokenIndex === 0 ? sentence.start_char : sentence.tokens[tokenIndex - 1].end_char
  return sentence.text.slice(previousEnd - sentence.start_char, token.start_char - sentence.start_char)
}

function tokenStyle(sentence: SentenceDef, tokenIndex: number) {
  const annotation = annotationForToken(sentence, tokenIndex)
  if (annotation) return { '--token-color': annotation.tag_color }
  if (isTokenInDrag(sentence, tokenIndex) && selectedTag.value) return { '--token-color': selectedTag.value.color }
  return {}
}

function exportJsonl() {
  if (!documentMeta.value) return
  window.location.href = `/api/projects/${PROJECT_ID}/documents/${documentMeta.value.id}/export.jsonl`
}

function clampIndex(index: number) {
  return Math.min(Math.max(index, 0), Math.max(sentences.value.length - 1, 0))
}

async function responseMessage(response: Response) {
  try {
    const payload = await response.json()
    return payload.detail ?? response.statusText
  } catch {
    return response.statusText
  }
}
</script>

<template>
  <main class="app-shell">
    <nav class="topbar" aria-label="Primary">
      <div class="brand-cluster">
        <div class="brand-mark" aria-hidden="true">A</div>
        <div>
          <span class="brand-name">AnnoPilot</span>
          <small>Persistent TXT annotation reader</small>
        </div>
      </div>

      <div class="run-status" aria-label="Runtime status">
        <CheckCircle :size="17" aria-hidden="true" />
        <span>SQLite + JSONL</span>
      </div>

      <button class="icon-button" aria-label="Open settings">
        <Settings :size="19" aria-hidden="true" />
      </button>
    </nav>

    <section class="workbench" aria-label="Annotation workspace">
      <aside class="side-panel tag-panel" aria-labelledby="tag-panel-title">
        <label class="upload-card">
          <Upload :size="22" aria-hidden="true" />
          <span>
            <strong>{{ isUploading ? 'Importing TXT...' : 'Import TXT' }}</strong>
            <small>UTF-8, up to 10 MB</small>
          </span>
          <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="handleImport" />
        </label>

        <div class="panel-heading tag-heading">
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
            :class="{ selected: tagItem.id === selectedTagId }"
            :style="{ '--tag-color': tagItem.color }"
            @click="selectedTagId = tagItem.id"
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
            @click="setCurrentSentence(sentence.index)"
          >
            <span>#{{ sentence.index + 1 }}</span>
            <strong>{{ sentence.completed ? 'Done' : sentence.index === currentSentenceIndex ? 'Active' : 'Pending' }}</strong>
          </button>
        </div>
      </aside>

      <section class="editor-panel" aria-labelledby="editor-title">
        <div class="editor-header">
          <div>
            <p class="section-kicker">Annotation Reader</p>
            <h1 id="editor-title">{{ documentMeta?.filename ?? 'Import a TXT file to begin' }}</h1>
          </div>
          <div class="document-pill">
            <Clock :size="16" aria-hidden="true" />
            <span>{{ currentSentence ? `Sentence ${currentSentence.index + 1}` : 'No document' }}</span>
          </div>
        </div>

        <p v-if="readerError" class="reader-error">{{ readerError }}</p>

        <article v-if="!documentMeta" class="empty-reader" aria-label="Empty reader state">
          <FileText :size="44" aria-hidden="true" />
          <h2>Import a TXT file</h2>
          <p>
            AnnoPilot will split it into sentences, tokenize each sentence, and keep annotation progress in SQLite
            while writing audit events to JSONL.
          </p>
        </article>

        <article v-else class="text-reader" aria-label="Scrollable text annotation reader">
          <section
            v-for="sentence in sentences"
            :key="sentence.id"
            :ref="(element) => setSentenceElement(sentence.id, element)"
            class="sentence-card"
            :class="{
              active: sentence.index === currentSentenceIndex,
              dimmed: sentence.index !== currentSentenceIndex,
              completed: sentence.completed,
            }"
            @click="onSentenceClick(sentence.index)"
          >
            <div class="sentence-meta">
              <span>#{{ sentence.index + 1 }}</span>
              <strong>{{ sentence.completed ? 'Completed' : sentence.index === currentSentenceIndex ? 'Active' : 'Waiting' }}</strong>
            </div>
            <p class="sentence-text">
              <template v-for="(token, tokenIndex) in sentence.tokens" :key="token.id">
                {{ tokenPrefix(sentence, tokenIndex) }}<button
                  class="token"
                  :class="{
                    annotated: annotationForToken(sentence, token.token_index),
                    selecting: isTokenInDrag(sentence, token.token_index),
                  }"
                  :style="tokenStyle(sentence, token.token_index)"
                  @pointerdown="onTokenPointerDown(sentence, token.token_index, $event)"
                  @pointerenter="onTokenPointerEnter(sentence, token.token_index)"
                  @pointerup="onTokenPointerUp(sentence, token.token_index)"
                >{{ token.text }}</button>
              </template>
            </p>
          </section>
        </article>

        <section class="candidate-card" aria-labelledby="candidate-title">
          <div class="candidate-heading">
            <h2 id="candidate-title">Current sentence spans</h2>
            <span>{{ activeAnnotations.length }} selected</span>
          </div>
          <div v-if="activeAnnotations.length" class="candidate-list">
            <button
              v-for="annotation in activeAnnotations"
              :key="annotation.id"
              class="candidate-row"
              :style="{ '--token-color': annotation.tag_color }"
              @click="deleteAnnotation(annotation.id)"
            >
              <span>
                <strong>{{ annotation.text }}</strong>
                <small>{{ annotation.tag_name }}</small>
              </span>
              <em>Remove</em>
            </button>
          </div>
          <p v-else class="candidate-empty">Select a tag, then click or drag tokens in the active sentence.</p>
        </section>

        <section class="verification-card" aria-label="Human verification actions">
          <div>
            <p class="section-kicker">Human Verification</p>
            <h2>Complete sentence and continue</h2>
          </div>
          <div class="verification-actions">
            <button class="accept-button" :disabled="!currentSentence || isSaving" @click="completeCurrentSentence">
              Complete · Enter
            </button>
            <button class="edit-button" :disabled="!currentSentence" @click="setCurrentSentence(currentSentenceIndex - 1)">
              Previous
            </button>
            <button class="skip-button" :disabled="!currentSentence" @click="setCurrentSentence(currentSentenceIndex + 1)">
              Next
            </button>
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

        <button class="export-button" :disabled="!documentMeta" @click="exportJsonl">
          Export JSONL
          <Download :size="18" aria-hidden="true" />
        </button>
      </aside>
    </section>
  </main>
</template>
