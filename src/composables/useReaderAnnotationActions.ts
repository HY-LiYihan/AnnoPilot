import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { createAnnotation, deleteAnnotation } from '../api/annotations'
import {
  PROJECT_ID,
  type AnnotationDef,
  type DragSelection,
  type DocumentMeta,
  type SentenceDef,
  type TagDef,
} from '../types/domain'
import { normalizedRange } from './useTokenSelection'

type SentenceAnswer = 'accept' | 'reject' | 'ignore'

type UndoableSpanAction =
  | {
      kind: 'created'
      label: string
      sentenceId: string
      createdAnnotationIds: string[]
      restoredAnnotations: AnnotationDef[]
    }
  | {
      kind: 'deleted'
      label: string
      sentenceId: string
      annotation: AnnotationDef
    }

type TokenSelectionState = {
  pendingSelection: Ref<DragSelection | null>
  setPendingSelection: (sentenceId: string, start: number, end: number) => void
}

type UseReaderAnnotationActionsOptions = {
  completeCurrentSentence: (answer?: SentenceAnswer) => Promise<void>
  currentSentence: ComputedRef<SentenceDef | null>
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  findMonoglossTag: () => TagDef | null
  isSaving: Ref<boolean>
  loadSentenceWindow: (documentId: string, targetIndex: number, force?: boolean) => Promise<void>
  readerError: Ref<string>
  refreshAuditSummary: () => Promise<void>
  refreshDocumentSummary: () => Promise<void>
  replaceSentenceAnnotations: (sentenceId: string, annotations: AnnotationDef[]) => void
  selectedTagId: Ref<string>
  selection: TokenSelectionState
  sentences: Ref<SentenceDef[]>
  tags: Ref<TagDef[]>
}

export function useReaderAnnotationActions(options: UseReaderAnnotationActionsOptions) {
  const lastUndoAction = ref<UndoableSpanAction | null>(null)
  const canUndoSpanAction = computed(() => Boolean(lastUndoAction.value))
  const undoLabel = computed(() => lastUndoAction.value?.label ?? 'Undo span')

  function selectCurrentSentenceSpan() {
    const sentence = options.currentSentence.value
    if (!sentence?.tokens.length) return
    const firstToken = sentence.tokens[0]
    const lastToken = sentence.tokens[sentence.tokens.length - 1]
    options.selection.setPendingSelection(sentence.id, firstToken.token_index, lastToken.token_index)
  }

  async function markCurrentSentenceMonogloss() {
    const sentence = options.currentSentence.value
    const tag = options.findMonoglossTag()
    if (!sentence?.tokens.length || options.isSaving.value) return
    if (!tag) {
      options.readerError.value = 'Monogloss label is not available in the current schema.'
      return
    }
    const firstToken = sentence.tokens[0]
    const lastToken = sentence.tokens[sentence.tokens.length - 1]
    options.selectedTagId.value = tag.id
    const created = await createSentenceAnnotation(sentence, firstToken.token_index, lastToken.token_index, tag.id)
    if (created) await options.completeCurrentSentence('accept')
  }

  function handleTagClick(tagId: string) {
    void applyTagToSelection(tagId)
  }

  async function applyTagToSelection(tagId: string) {
    options.selectedTagId.value = tagId
    const pendingSelection = options.selection.pendingSelection.value
    if (!pendingSelection) return
    const sentence = options.sentences.value.find((item) => item.id === pendingSelection.sentenceId)
    if (!sentence) return
    await createSentenceAnnotation(sentence, pendingSelection.start, pendingSelection.end, tagId)
  }

  async function createSentenceAnnotation(sentence: SentenceDef, start: number, end: number, tagId: string) {
    const tag = options.tags.value.find((tagItem) => tagItem.id === tagId)
    if (!tag || options.isSaving.value) return false
    options.isSaving.value = true
    options.readerError.value = ''
    const [startTokenIndex, endTokenIndex] = normalizedRange(start, end)
    try {
      const previousAnnotationIds = new Set(sentence.annotations.map((annotation) => annotation.id))
      const overlaps = overlappingAnnotations(sentence, startTokenIndex, endTokenIndex)
      await Promise.all(overlaps.map((annotation) => deleteAnnotation(PROJECT_ID, annotation.id)))
      const payload = await createAnnotation(PROJECT_ID, sentence.id, tag.id, startTokenIndex, endTokenIndex)
      const createdAnnotationIds = payload.annotations
        .filter((annotation) => !previousAnnotationIds.has(annotation.id))
        .map((annotation) => annotation.id)
      options.replaceSentenceAnnotations(sentence.id, payload.annotations)
      options.selection.pendingSelection.value = null
      lastUndoAction.value = {
        kind: 'created',
        label: `Undo ${tag.name}`,
        sentenceId: sentence.id,
        createdAnnotationIds,
        restoredAnnotations: overlaps.filter((annotation) => annotation.source === 'human'),
      }
      await options.refreshDocumentSummary()
      await options.refreshAuditSummary()
      return true
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not save annotation.'
      return false
    } finally {
      options.isSaving.value = false
    }
  }

  async function removeAnnotation(annotationId: string) {
    if (options.isSaving.value) return
    const removedFromSentence = options.sentences.value.find((sentence) => sentence.annotations.some((annotation) => annotation.id === annotationId))
    const removedAnnotation = removedFromSentence?.annotations.find((annotation) => annotation.id === annotationId)
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      await deleteAnnotation(PROJECT_ID, annotationId)
      options.sentences.value = options.sentences.value.map((sentence) => ({
        ...sentence,
        annotations: sentence.annotations.filter((annotation) => annotation.id !== annotationId),
      }))
      lastUndoAction.value = removedFromSentence && removedAnnotation?.source === 'human'
        ? {
            kind: 'deleted',
            label: `Restore ${removedAnnotation.tag_name}`,
            sentenceId: removedFromSentence.id,
            annotation: removedAnnotation,
          }
        : null
      await options.refreshDocumentSummary()
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not delete annotation.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function undoLastSpanAction() {
    const action = lastUndoAction.value
    if (!action || !options.documentMeta.value || options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      if (action.kind === 'created') {
        await Promise.all(action.createdAnnotationIds.map((annotationId) => deleteAnnotation(PROJECT_ID, annotationId)))
        for (const annotation of action.restoredAnnotations) {
          await createAnnotation(
            PROJECT_ID,
            action.sentenceId,
            annotation.tag_id,
            annotation.start_token_index,
            annotation.end_token_index,
          )
        }
      } else {
        const loadedSentence = options.sentences.value.find((sentence) => sentence.id === action.sentenceId)
        const overlaps = loadedSentence
          ? overlappingAnnotations(loadedSentence, action.annotation.start_token_index, action.annotation.end_token_index)
          : []
        if (overlaps.length) {
          options.readerError.value = 'Cannot undo because that span now overlaps another annotation.'
          return
        }
        await createAnnotation(
          PROJECT_ID,
          action.sentenceId,
          action.annotation.tag_id,
          action.annotation.start_token_index,
          action.annotation.end_token_index,
        )
      }
      lastUndoAction.value = null
      await options.refreshDocumentSummary()
      await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not undo the last span action.'
    } finally {
      options.isSaving.value = false
    }
  }

  function resetAnnotationActionState() {
    lastUndoAction.value = null
  }

  return {
    applyTagToSelection,
    canUndoSpanAction,
    handleTagClick,
    markCurrentSentenceMonogloss,
    removeAnnotation,
    resetAnnotationActionState,
    selectCurrentSentenceSpan,
    undoLabel,
    undoLastSpanAction,
  }
}

function overlappingAnnotations(sentence: SentenceDef, start: number, end: number) {
  return sentence.annotations.filter(
    (annotation) => annotation.start_token_index <= end && annotation.end_token_index >= start,
  )
}
