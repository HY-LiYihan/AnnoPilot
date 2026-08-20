import type { SuggestionDef, TagDef } from '../types/domain'

type SentenceAnswer = 'accept' | 'reject' | 'ignore'

type ReadableValue<T> = {
  value: T
}

type KeyboardShortcutTarget = {
  isContentEditable?: boolean
  matches?: (selector: string) => boolean
}

export type ReaderKeyboardEvent = {
  altKey?: boolean
  code?: string
  ctrlKey?: boolean
  key: string
  metaKey?: boolean
  preventDefault: () => void
  shiftKey?: boolean
  target?: KeyboardShortcutTarget | null
}

export type KeyboardShortcutActions = {
  acceptCurrentSentenceSuggestions: () => void | Promise<void>
  acceptSuggestedSpan: (suggestion: SuggestionDef) => void | Promise<void>
  applyTagToSelection: (tagId: string) => void | Promise<void>
  completeCurrentSentence: (answer?: SentenceAnswer) => void | Promise<void>
  cycleActiveSuggestionTarget: (direction: 1 | -1) => void
  jumpToNextReviewSentence: () => void
  markCurrentSentenceMonogloss: () => void | Promise<void>
  removeHoveredAnnotation?: () => void | Promise<void>
  rejectCurrentSentenceSuggestions: () => void | Promise<void>
  rejectSuggestedSpan: (suggestion: SuggestionDef) => void | Promise<void>
  reopenCurrentSentence: () => void | Promise<void>
  selectCurrentSentenceSpan: () => void
  setCurrentSentence: (index: number, scrollBehavior?: ScrollBehavior, targetSuggestionId?: string) => void
  undoLastSpanAction: () => void | Promise<void>
}

export type ReaderKeyboardShortcutOptions = KeyboardShortcutActions & {
  assistanceDraftActive?: ReadableValue<boolean>
  activeSuggestion: ReadableValue<SuggestionDef | null>
  activeSuggestions: ReadableValue<SuggestionDef[]>
  currentSentenceIndex: ReadableValue<number>
  hoveredAnnotationId?: ReadableValue<string>
  tags: ReadableValue<TagDef[]>
}

function matchesTarget(target: KeyboardShortcutTarget | null | undefined, selector: string) {
  return Boolean(target?.matches?.(selector))
}

export function handleReaderKeyboardShortcut(
  event: ReaderKeyboardEvent,
  options: ReaderKeyboardShortcutOptions,
) {
  const target = event.target
  if (
    matchesTarget(target, 'input, textarea, select')
    || matchesTarget(target, 'button, a')
    || target?.isContentEditable
  ) return false

  const key = event.key
  const lowerKey = key.toLowerCase()

  if ((event.metaKey || event.ctrlKey) && lowerKey === 'z') {
    if (options.assistanceDraftActive?.value) return false
    event.preventDefault()
    void options.undoLastSpanAction()
    return true
  }

  if (event.metaKey || event.ctrlKey || event.altKey) return false

  if (key === 'Tab' && !options.assistanceDraftActive?.value && options.activeSuggestions.value.length > 0) {
    event.preventDefault()
    options.cycleActiveSuggestionTarget(event.shiftKey ? -1 : 1)
    return true
  }

  const shortcutTag = options.tags.value.find((tagItem) => tagItem.shortcut === key)
  if (shortcutTag) {
    event.preventDefault()
    void options.applyTagToSelection(shortcutTag.id)
    return true
  }

  if (key === 'Enter') {
    event.preventDefault()
    void options.completeCurrentSentence()
    return true
  }

  if ((event.code === 'Space' || key === ' ') && options.hoveredAnnotationId?.value) {
    event.preventDefault()
    void options.removeHoveredAnnotation?.()
    return true
  }

  if (options.assistanceDraftActive?.value) {
    if (lowerKey === 'i' || lowerKey === 'j' || event.code === 'Space' || key === ' ') {
      event.preventDefault()
      void options.completeCurrentSentence('ignore')
      return true
    }
    if (key === 'ArrowDown') {
      event.preventDefault()
      options.setCurrentSentence(options.currentSentenceIndex.value + 1)
      return true
    }
    if (key === 'ArrowUp') {
      event.preventDefault()
      options.setCurrentSentence(options.currentSentenceIndex.value - 1)
      return true
    }
    return false
  }

  if (lowerKey === 'i') {
    event.preventDefault()
    void options.completeCurrentSentence('ignore')
    return true
  }

  if (lowerKey === 'j') {
    event.preventDefault()
    void options.completeCurrentSentence('reject')
    return true
  }

  if (lowerKey === 'e') {
    event.preventDefault()
    void options.reopenCurrentSentence()
    return true
  }

  if (lowerKey === 's') {
    event.preventDefault()
    options.selectCurrentSentenceSpan()
    return true
  }

  if (lowerKey === 'm') {
    event.preventDefault()
    void options.markCurrentSentenceMonogloss()
    return true
  }

  if (event.code === 'Space' || key === ' ') {
    event.preventDefault()
    void options.completeCurrentSentence('ignore')
    return true
  }

  if (key === 'ArrowDown') {
    event.preventDefault()
    options.setCurrentSentence(options.currentSentenceIndex.value + 1)
    return true
  }

  if (key === 'ArrowUp') {
    event.preventDefault()
    options.setCurrentSentence(options.currentSentenceIndex.value - 1)
    return true
  }

  if (lowerKey === 'a') {
    event.preventDefault()
    void options.acceptCurrentSentenceSuggestions()
    return true
  }

  if (lowerKey === 'x') {
    event.preventDefault()
    void options.rejectCurrentSentenceSuggestions()
    return true
  }

  if (lowerKey === 'y') {
    event.preventDefault()
    const suggestion = options.activeSuggestion.value
    if (suggestion) void options.acceptSuggestedSpan(suggestion)
    return true
  }

  if (lowerKey === 'n') {
    event.preventDefault()
    const suggestion = options.activeSuggestion.value
    if (suggestion) void options.rejectSuggestedSpan(suggestion)
    return true
  }

  if (lowerKey === 'r') {
    event.preventDefault()
    options.jumpToNextReviewSentence()
    return true
  }

  return false
}
