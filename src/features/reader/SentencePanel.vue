<script setup lang="ts">
import { Clock, FileText, Upload } from '@lucide/vue'
import type { AnnotationDef, DocumentMeta, DragSelection, SentenceDef } from '../../types/domain'

defineProps<{
  documentMeta: DocumentMeta | null
  currentSentence: SentenceDef | null
  currentSentenceIndex: number
  sentences: SentenceDef[]
  activeAnnotations: AnnotationDef[]
  pendingSelection: DragSelection | null
  pendingSelectionText: string
  readerError: string
  isUploading: boolean
  isSaving: boolean
  annotationForToken: (sentence: SentenceDef, tokenIndex: number) => AnnotationDef | undefined
  isTokenInDrag: (sentence: SentenceDef, tokenIndex: number) => boolean
  isTokenPending: (sentence: SentenceDef, tokenIndex: number) => boolean
  tokenPrefix: (sentence: SentenceDef, tokenIndex: number) => string
  tokenStyle: (sentence: SentenceDef, tokenIndex: number) => Record<string, string>
}>()

const emit = defineEmits<{
  import: [event: Event]
  'set-sentence-element': [sentenceId: string, element: unknown]
  'sentence-click': [sentenceIndex: number]
  'token-pointer-down': [sentence: SentenceDef, tokenIndex: number, event: PointerEvent]
  'token-pointer-enter': [sentence: SentenceDef, tokenIndex: number]
  'token-pointer-up': [sentence: SentenceDef, tokenIndex: number]
  'delete-annotation': [annotationId: string]
  complete: []
  previous: []
  next: []
}>()
</script>

<template>
  <section class="editor-panel" aria-labelledby="editor-title">
    <div class="editor-header">
      <div>
        <p class="section-kicker">Annotation Reader</p>
        <h1 id="editor-title">{{ documentMeta?.filename ?? 'Import a TXT file to begin' }}</h1>
      </div>
      <div class="editor-tools">
        <label class="upload-card editor-upload">
          <Upload :size="19" aria-hidden="true" />
          <span>
            <strong>{{ isUploading ? 'Importing...' : 'Import TXT' }}</strong>
            <small>UTF-8 · 10 MB</small>
          </span>
          <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="emit('import', $event)" />
        </label>
        <div class="document-pill">
          <Clock :size="16" aria-hidden="true" />
          <span>{{ currentSentence ? `Sentence ${currentSentence.index + 1}` : 'No document' }}</span>
        </div>
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
      <label class="upload-card empty-upload">
        <Upload :size="22" aria-hidden="true" />
        <span>
          <strong>{{ isUploading ? 'Importing TXT...' : 'Choose TXT from Downloads' }}</strong>
          <small>Pick your prepared test file</small>
        </span>
        <input type="file" accept=".txt,text/plain" :disabled="isUploading" @change="emit('import', $event)" />
      </label>
    </article>

    <article v-else class="text-reader" aria-label="Scrollable text annotation reader">
      <section
        v-for="sentence in sentences"
        :key="sentence.id"
        :ref="(element) => emit('set-sentence-element', sentence.id, element)"
        class="sentence-card"
        :class="{
          active: sentence.index === currentSentenceIndex,
          dimmed: sentence.index !== currentSentenceIndex,
          completed: sentence.completed,
        }"
        @click="emit('sentence-click', sentence.index)"
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
                pending: isTokenPending(sentence, token.token_index),
              }"
              :style="tokenStyle(sentence, token.token_index)"
              @pointerdown="emit('token-pointer-down', sentence, token.token_index, $event)"
              @pointerenter="emit('token-pointer-enter', sentence, token.token_index)"
              @pointerup="emit('token-pointer-up', sentence, token.token_index)"
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
      <div v-if="pendingSelection && pendingSelectionText" class="pending-card">
        <span>
          <strong>{{ pendingSelectionText }}</strong>
          <small>Press 1-6 or click a tag on the left to apply</small>
        </span>
        <em>Pending</em>
      </div>
      <div v-if="activeAnnotations.length" class="candidate-list">
        <button
          v-for="annotation in activeAnnotations"
          :key="annotation.id"
          class="candidate-row"
          :style="{ '--token-color': annotation.tag_color }"
          @click="emit('delete-annotation', annotation.id)"
        >
          <span>
            <strong>{{ annotation.text }}</strong>
            <small>{{ annotation.tag_name }}</small>
          </span>
          <em>Remove</em>
        </button>
      </div>
      <p v-else class="candidate-empty">Select a word or span first, then press 1-6 or click a tag to label it.</p>
    </section>

    <section class="verification-card" aria-label="Human verification actions">
      <div>
        <p class="section-kicker">Human Verification</p>
        <h2>Complete sentence and continue</h2>
      </div>
      <div class="verification-actions">
        <button class="accept-button" :disabled="!currentSentence || isSaving" @click="emit('complete')">
          Complete · Enter
        </button>
        <button class="edit-button" :disabled="!currentSentence" @click="emit('previous')">Previous</button>
        <button class="skip-button" :disabled="!currentSentence" @click="emit('next')">Next</button>
      </div>
    </section>
  </section>
</template>
