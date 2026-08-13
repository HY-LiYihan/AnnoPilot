import { onBeforeUnmount, onMounted, type ComputedRef, type Ref } from 'vue'
import type { SuggestionDef, TagDef } from '../types/domain'

type SentenceAnswer = 'accept' | 'reject' | 'ignore'

type KeyboardShortcutActions = {
  acceptCurrentSentenceSuggestions: () => void | Promise<void>
  acceptSuggestedSpan: (suggestion: SuggestionDef) => void | Promise<void>
  applyTagToSelection: (tagId: string) => void | Promise<void>
  completeCurrentSentence: (answer?: SentenceAnswer) => void | Promise<void>
  cycleActiveSuggestionTarget: (direction: 1 | -1) => void
  jumpToNextReviewSentence: () => void
  rejectCurrentSentenceSuggestions: () => void | Promise<void>
  rejectSuggestedSpan: (suggestion: SuggestionDef) => void | Promise<void>
  reopenCurrentSentence: () => void | Promise<void>
  setCurrentSentence: (index: number, scrollBehavior?: ScrollBehavior) => void
  undoLastSpanAction: () => void | Promise<void>
}

type ReaderKeyboardShortcutOptions = KeyboardShortcutActions & {
  activeSuggestion: ComputedRef<SuggestionDef | null>
  activeSuggestions: ComputedRef<SuggestionDef[]>
  currentSentenceIndex: Ref<number>
  tags: Ref<TagDef[]>
}

export function useReaderKeyboardShortcuts(options: ReaderKeyboardShortcutOptions) {
  function handleKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null
    if (target?.matches('input, textarea, select') || target?.isContentEditable) return
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault()
      void options.undoLastSpanAction()
      return
    }
    if (event.key === 'Tab' && options.activeSuggestions.value.length > 0) {
      event.preventDefault()
      options.cycleActiveSuggestionTarget(event.shiftKey ? -1 : 1)
      return
    }
    const shortcutTag = options.tags.value.find((tagItem) => tagItem.shortcut === event.key)
    if (shortcutTag) {
      event.preventDefault()
      void options.applyTagToSelection(shortcutTag.id)
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      void options.completeCurrentSentence()
      return
    }
    if (event.key.toLowerCase() === 'i') {
      event.preventDefault()
      void options.completeCurrentSentence('ignore')
      return
    }
    if (event.key.toLowerCase() === 'j') {
      event.preventDefault()
      void options.completeCurrentSentence('reject')
      return
    }
    if (event.key.toLowerCase() === 'e') {
      event.preventDefault()
      void options.reopenCurrentSentence()
      return
    }
    if ((event.code === 'Space' || event.key === ' ') && !target?.matches('button, a')) {
      event.preventDefault()
      void options.completeCurrentSentence('ignore')
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      options.setCurrentSentence(options.currentSentenceIndex.value + 1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      options.setCurrentSentence(options.currentSentenceIndex.value - 1)
      return
    }
    if (event.key.toLowerCase() === 'a') {
      event.preventDefault()
      void options.acceptCurrentSentenceSuggestions()
      return
    }
    if (event.key.toLowerCase() === 'x') {
      event.preventDefault()
      void options.rejectCurrentSentenceSuggestions()
      return
    }
    if (event.key.toLowerCase() === 'y') {
      event.preventDefault()
      const suggestion = options.activeSuggestion.value
      if (suggestion) void options.acceptSuggestedSpan(suggestion)
      return
    }
    if (event.key.toLowerCase() === 'n') {
      event.preventDefault()
      const suggestion = options.activeSuggestion.value
      if (suggestion) void options.rejectSuggestedSpan(suggestion)
      return
    }
    if (event.key.toLowerCase() === 'r') {
      event.preventDefault()
      options.jumpToNextReviewSentence()
    }
  }

  onMounted(() => {
    window.addEventListener('keydown', handleKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleKeydown)
  })
}
