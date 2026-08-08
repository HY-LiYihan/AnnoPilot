import { computed, ref, type Ref } from 'vue'
import type { DragSelection, SentenceDef } from '../types/domain'

export function useTokenSelection(sentences: Ref<SentenceDef[]>) {
  const dragSelection = ref<DragSelection | null>(null)
  const pendingSelection = ref<DragSelection | null>(null)

  const pendingSelectionText = computed(() => {
    const selection = pendingSelection.value
    if (!selection) return ''
    const sentence = sentences.value.find((item) => item.id === selection.sentenceId)
    if (!sentence) return ''
    const [startIndex, endIndex] = normalizedRange(selection.start, selection.end)
    const startToken = sentence.tokens[startIndex]
    const endToken = sentence.tokens[endIndex]
    if (!startToken || !endToken) return ''
    return sentence.text.slice(startToken.start_char - sentence.start_char, endToken.end_char - sentence.start_char)
  })

  function beginSelection(sentenceId: string, tokenIndex: number) {
    pendingSelection.value = null
    dragSelection.value = { sentenceId, start: tokenIndex, end: tokenIndex }
  }

  function extendSelection(sentenceId: string, tokenIndex: number) {
    if (dragSelection.value?.sentenceId !== sentenceId) return
    dragSelection.value = { ...dragSelection.value, end: tokenIndex }
  }

  function finishSelection(sentenceId: string, tokenIndex: number) {
    const selection = dragSelection.value
    if (!selection || selection.sentenceId !== sentenceId) return
    pendingSelection.value = { sentenceId, start: selection.start, end: tokenIndex }
    dragSelection.value = null
  }

  function isTokenInDrag(sentence: SentenceDef, tokenIndex: number) {
    const selection = dragSelection.value
    if (!selection || selection.sentenceId !== sentence.id) return false
    const [start, end] = normalizedRange(selection.start, selection.end)
    return tokenIndex >= start && tokenIndex <= end
  }

  function isTokenPending(sentence: SentenceDef, tokenIndex: number) {
    const selection = pendingSelection.value
    if (!selection || selection.sentenceId !== sentence.id) return false
    const [start, end] = normalizedRange(selection.start, selection.end)
    return tokenIndex >= start && tokenIndex <= end
  }

  function clearSelection() {
    dragSelection.value = null
    pendingSelection.value = null
  }

  return {
    dragSelection,
    pendingSelection,
    pendingSelectionText,
    beginSelection,
    extendSelection,
    finishSelection,
    isTokenInDrag,
    isTokenPending,
    clearSelection,
  }
}

export function normalizedRange(start: number, end: number) {
  return [Math.min(start, end), Math.max(start, end)] as const
}
