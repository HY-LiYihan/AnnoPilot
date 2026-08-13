import { onBeforeUnmount, onMounted } from 'vue'
import {
  handleReaderKeyboardShortcut,
  type ReaderKeyboardEvent,
  type ReaderKeyboardShortcutOptions,
} from './readerKeyboardDispatch'

export function useReaderKeyboardShortcuts(options: ReaderKeyboardShortcutOptions) {
  function handleKeydown(event: KeyboardEvent) {
    handleReaderKeyboardShortcut(event as ReaderKeyboardEvent, options)
  }

  onMounted(() => {
    window.addEventListener('keydown', handleKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleKeydown)
  })
}
