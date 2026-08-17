import { computed, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'
import { AssistanceApiError, decideAssistance, fetchAssistanceStatus, updateAssistanceSettings } from '../api/assistance.ts'
import type {
  AssistanceDecisionAction,
  AssistanceDecisionResponse,
  AssistanceErrorReason,
  AssistanceFinalSpan,
  AssistanceQueue,
  AssistanceQueueItem,
  AssistanceSpan,
  AssistanceStatus,
} from '../types/domain.ts'

export type LocalAssistanceDraftSpan = Pick<AssistanceSpan, 'suggestion_id' | 'tag_id' | 'start_token_index' | 'end_token_index'>
type AssistanceDraftSeed = Pick<AssistanceSpan, 'tag_id' | 'start_token_index' | 'end_token_index'>
  & Partial<Pick<AssistanceSpan, 'suggestion_id'>>

export type AssistanceNavigationTarget = {
  sentenceId: string
  sentenceIndex: number | null
}

type UseReaderAssistanceOptions = {
  projectId: MaybeRefOrGetter<string>
  documentId: Ref<string | null>
  currentSentenceId?: Ref<string | null>
  pollingIntervalMs?: number
  autoStart?: boolean
  onDecision?: (target: AssistanceNavigationTarget | null, response: AssistanceDecisionResponse) => void | Promise<void>
}

const emptyQueue: AssistanceQueue = {
  counts: {},
  items: [],
  ready: 0,
  running: 0,
  queued: 0,
  skipped: 0,
  failed: 0,
}

export function initializeAssistanceDraft(spans: readonly AssistanceDraftSeed[]): LocalAssistanceDraftSpan[] {
  return spans
    .map((span) => ({
      suggestion_id: span.suggestion_id ?? null,
      tag_id: span.tag_id,
      start_token_index: Math.min(span.start_token_index, span.end_token_index),
      end_token_index: Math.max(span.start_token_index, span.end_token_index),
    }))
    .sort(compareDraftSpans)
}

export function replaceOverlappingDraftSpan(
  spans: readonly LocalAssistanceDraftSpan[],
  nextSpan: AssistanceFinalSpan,
): LocalAssistanceDraftSpan[] {
  const normalized = {
    suggestion_id: null,
    tag_id: nextSpan.tag_id,
    start_token_index: Math.min(nextSpan.start_token_index, nextSpan.end_token_index),
    end_token_index: Math.max(nextSpan.start_token_index, nextSpan.end_token_index),
  }
  return [...spans.filter((span) => !rangesOverlap(span, normalized)), normalized].sort(compareDraftSpans)
}

export function draftSpansEqual(
  left: readonly LocalAssistanceDraftSpan[],
  right: readonly LocalAssistanceDraftSpan[],
): boolean {
  if (left.length !== right.length) return false
  const normalizedLeft = initializeAssistanceDraft(left)
  const normalizedRight = initializeAssistanceDraft(right)
  return normalizedLeft.every((span, index) => {
    const other = normalizedRight[index]
    return span.tag_id === other.tag_id
      && span.start_token_index === other.start_token_index
      && span.end_token_index === other.end_token_index
  })
}

function rangesOverlap(left: AssistanceFinalSpan, right: AssistanceFinalSpan): boolean {
  return left.start_token_index <= right.end_token_index && right.start_token_index <= left.end_token_index
}

function compareDraftSpans(left: LocalAssistanceDraftSpan, right: LocalAssistanceDraftSpan): number {
  return left.start_token_index - right.start_token_index
    || left.end_token_index - right.end_token_index
    || left.tag_id.localeCompare(right.tag_id)
}

export function useReaderAssistance(options: UseReaderAssistanceOptions) {
  const status = ref<AssistanceStatus | null>(null)
  const error = ref('')
  const isLoading = ref(false)
  const isDeciding = ref(false)
  const localCurrentSentenceId = ref<string | null>(null)
  const localDraftSpans = ref<LocalAssistanceDraftSpan[]>([])
  const originalDraftSpans = ref<LocalAssistanceDraftSpan[]>([])
  const errorReasons = ref<AssistanceErrorReason[]>([])
  const errorNote = ref('')
  const lastDecision = ref<{ response: AssistanceDecisionResponse; next: AssistanceNavigationTarget | null } | null>(null)
  let pollingTimer: ReturnType<typeof setInterval> | null = null
  let refreshSerial = 0

  const currentSentenceId = computed(() => options.currentSentenceId?.value ?? localCurrentSentenceId.value)
  const queue = computed(() => status.value?.queue ?? emptyQueue)
  const currentDraft = computed<AssistanceQueueItem | null>(() => {
    const sentenceId = currentSentenceId.value
    if (!sentenceId) return null
    return queue.value.items.find((item) => item.sentence_id === sentenceId && (item.status === 'ready' || item.status === 'skipped')) ?? null
  })
  const draftKey = computed(() => currentDraft.value ? `${currentDraft.value.draft_id}:${currentDraft.value.draft_version}` : '')
  const isDraftModified = computed(() => !draftSpansEqual(localDraftSpans.value, originalDraftSpans.value))
  const queueCounts = computed(() => ({ ...queue.value.counts, ready: queue.value.ready, running: queue.value.running, queued: queue.value.queued, skipped: queue.value.skipped, failed: queue.value.failed }))
  const tagProgress = computed(() => status.value?.tag_progress ?? [])

  watch(draftKey, () => resetLocalDraft())
  watch(options.documentId, (documentId) => {
    clearDocumentState()
    if (documentId) void refresh()
  }, { immediate: true })

  function clearDocumentState() {
    refreshSerial += 1
    status.value = null
    error.value = ''
    lastDecision.value = null
    resetLocalDraft()
  }

  function resetLocalDraft() {
    const spans = currentDraft.value ? initializeAssistanceDraft(currentDraft.value.spans) : []
    originalDraftSpans.value = spans
    localDraftSpans.value = initializeAssistanceDraft(spans)
    errorReasons.value = []
    errorNote.value = ''
  }

  function setCurrentSentenceId(sentenceId: string | null) {
    localCurrentSentenceId.value = sentenceId
  }

  function setLocalDraftTokenRange(tagId: string, startTokenIndex: number, endTokenIndex: number) {
    if (!currentDraft.value) return
    localDraftSpans.value = replaceOverlappingDraftSpan(localDraftSpans.value, {
      tag_id: tagId,
      start_token_index: startTokenIndex,
      end_token_index: endTokenIndex,
    })
  }

  function removeLocalDraftSpan(span: LocalAssistanceDraftSpan | number) {
    const index = typeof span === 'number'
      ? span
      : localDraftSpans.value.findIndex((item) => item === span || sameDraftSpan(item, span))
    if (index < 0) return
    localDraftSpans.value = localDraftSpans.value.filter((_, itemIndex) => itemIndex !== index)
  }

  async function refresh() {
    const documentId = options.documentId.value
    if (!documentId) return null
    const serial = ++refreshSerial
    isLoading.value = true
    error.value = ''
    try {
      const payload = await fetchAssistanceStatus(toValue(options.projectId), documentId)
      if (serial !== refreshSerial || documentId !== options.documentId.value) return null
      status.value = payload
      return payload
    } catch (cause) {
      if (serial === refreshSerial) error.value = messageFor(cause, 'Could not refresh assistance status.')
      throw cause
    } finally {
      if (serial === refreshSerial) isLoading.value = false
    }
  }

  async function setEnabled(enabled: boolean) {
    const documentId = options.documentId.value
    if (!documentId) return null
    isLoading.value = true
    error.value = ''
    try {
      const payload = await updateAssistanceSettings(toValue(options.projectId), documentId, enabled)
      if (documentId === options.documentId.value) status.value = payload
      return payload
    } catch (cause) {
      error.value = messageFor(cause, 'Could not update assistance settings.')
      throw cause
    } finally {
      isLoading.value = false
    }
  }

  async function confirmDraft() {
    return decideCurrentDraft('confirm')
  }

  async function correctDraft(reasons = errorReasons.value, note = errorNote.value) {
    return decideCurrentDraft('correct', reasons, note)
  }

  async function skipDraft() {
    return decideCurrentDraft('skip')
  }

  async function decideCurrentDraft(
    action: AssistanceDecisionAction,
    reasons: AssistanceErrorReason[] = [],
    note = '',
  ) {
    const draft = currentDraft.value
    if (!draft) throw new Error('No ready assistance draft for the current sentence.')
    const documentId = options.documentId.value
    if (!documentId || isDeciding.value) return null

    isDeciding.value = true
    error.value = ''
    try {
      const payload = await decideAssistance(toValue(options.projectId), draft.sentence_id, {
        action,
        draft_id: draft.draft_id,
        draft_version: draft.draft_version,
        ...(action === 'correct' ? {
          final_spans: localDraftSpans.value.map(toFinalSpan),
          error_reasons: [...reasons],
          error_note: note || undefined,
        } : {}),
      })
      if (documentId === options.documentId.value && status.value) {
        status.value = { ...status.value, queue: payload.queue }
      }
      const next = findNavigationTarget(payload.next_sentence_id, payload.queue)
      lastDecision.value = { response: payload, next }
      resetLocalDraft()
      void refresh().catch(() => undefined)
      await options.onDecision?.(next, payload)
      return { response: payload, next }
    } catch (cause) {
      // A conflict deliberately leaves the frozen draft and local edits untouched for recovery.
      error.value = messageFor(cause, 'Could not save assistance decision.')
      throw cause
    } finally {
      isDeciding.value = false
    }
  }

  function startPolling() {
    stopPolling()
    const interval = Math.max(options.pollingIntervalMs ?? 2500, 500)
    pollingTimer = setInterval(() => {
      if (!isLoading.value && !isDeciding.value && options.documentId.value) void refresh().catch(() => undefined)
    }, interval)
  }

  function stopPolling() {
    if (pollingTimer) clearInterval(pollingTimer)
    pollingTimer = null
  }

  if (options.autoStart !== false) startPolling()
  onScopeDispose(stopPolling)

  return {
    status,
    error,
    isLoading,
    isDeciding,
    currentSentenceId,
    currentDraft,
    localDraftSpans,
    originalDraftSpans,
    errorReasons,
    errorNote,
    isDraftModified,
    queue,
    queueCounts,
    tagProgress,
    lastDecision,
    clearDocumentState,
    setCurrentSentenceId,
    setLocalDraftTokenRange,
    removeLocalDraftSpan,
    resetLocalDraft,
    refresh,
    setEnabled,
    confirmDraft,
    correctDraft,
    skipDraft,
    startPolling,
    stopPolling,
  }
}

function sameDraftSpan(left: LocalAssistanceDraftSpan, right: LocalAssistanceDraftSpan) {
  return left.tag_id === right.tag_id
    && left.start_token_index === right.start_token_index
    && left.end_token_index === right.end_token_index
}

function toFinalSpan(span: LocalAssistanceDraftSpan): AssistanceFinalSpan {
  return {
    tag_id: span.tag_id,
    start_token_index: span.start_token_index,
    end_token_index: span.end_token_index,
  }
}

function findNavigationTarget(nextSentenceId: string | null | undefined, queue: AssistanceQueue): AssistanceNavigationTarget | null {
  if (!nextSentenceId) return null
  const item = queue.items.find((candidate) => candidate.sentence_id === nextSentenceId)
  return { sentenceId: nextSentenceId, sentenceIndex: item?.sentence_index ?? null }
}

function messageFor(cause: unknown, fallback: string) {
  if (cause instanceof AssistanceApiError) return cause.message
  return cause instanceof Error ? cause.message : fallback
}
