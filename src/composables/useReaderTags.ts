import { computed, ref, type Ref } from 'vue'
import { createTag, deleteTag as deleteProjectTag, fetchTags, importTagSchema, renameTag as renameProjectTag } from '../api/tags'
import { PROJECT_ID, fallbackTags, type DocumentMeta, type TagDef } from '../types/domain'

type TagSelection = 'first' | 'preserve' | { tagId: string }

type UseReaderTagsOptions = {
  currentSentenceIndex: Ref<number>
  documentMeta: Ref<DocumentMeta | null>
  isSaving: Ref<boolean>
  loadDocument: (documentId: string, preserveCurrent?: boolean) => Promise<void>
  loadSentenceWindow: (documentId: string, targetIndex: number, force?: boolean) => Promise<void>
  readerError: Ref<string>
  refreshAuditSummary: () => Promise<void>
  refreshDocumentSummary: () => Promise<void>
}

export function useReaderTags(options: UseReaderTagsOptions) {
  const tags = ref<TagDef[]>(fallbackTags)
  const selectedTagId = ref(fallbackTags[0]?.id ?? '')
  const selectedTag = computed(() => tags.value.find((tagItem) => tagItem.id === selectedTagId.value) ?? tags.value[0] ?? null)

  function setTags(nextTags: TagDef[], selection: TagSelection = 'preserve') {
    tags.value = nextTags
    const preferredTagId = resolvePreferredTagId(nextTags, selection)
    selectedTagId.value = nextTags.find((tagItem) => tagItem.id === preferredTagId)?.id ?? nextTags[0]?.id ?? ''
  }

  async function loadProjectTags() {
    try {
      const payload = await fetchTags(PROJECT_ID)
      setTags(payload.tags)
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not load project tags.'
    }
  }

  async function addTag(name: string, description = '') {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await createTag(PROJECT_ID, name, description)
      tags.value = [...tags.value, payload.tag]
      selectedTagId.value = payload.tag.id
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not create tag.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function renameTag(tag: TagDef, name: string, description?: string | null, examples?: string[]) {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const payload = await renameProjectTag(PROJECT_ID, tag.id, name, description, examples)
      tags.value = tags.value.map((tagItem) => (tagItem.id === tag.id ? payload.tag : tagItem))
      if (options.documentMeta.value) {
        await options.refreshDocumentSummary()
        await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      }
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not rename tag.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function handleTagSchemaImport(file: File) {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      const schema = parseTagSchemaImportPayload(await file.text())
      const payload = await importTagSchema(PROJECT_ID, schema)
      setTags(payload.tags)
      if (options.documentMeta.value) {
        await options.refreshDocumentSummary()
        await options.loadSentenceWindow(options.documentMeta.value.id, options.currentSentenceIndex.value, true)
      }
      await options.refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not import tag schema.'
    } finally {
      options.isSaving.value = false
    }
  }

  async function deleteTag(tag: TagDef) {
    if (options.isSaving.value) return
    options.isSaving.value = true
    options.readerError.value = ''
    try {
      await deleteProjectTag(PROJECT_ID, tag.id)
      if (options.documentMeta.value) {
        await options.loadDocument(options.documentMeta.value.id, true)
      } else {
        setTags(tags.value.filter((tagItem) => tagItem.id !== tag.id))
      }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not delete tag.'
    } finally {
      options.isSaving.value = false
    }
  }

  function findMonoglossTag() {
    return tags.value.find((tag) =>
      tag.id === 'engagement_monogloss' ||
        tag.id.toLowerCase().includes('monogloss') ||
        tag.name.toLowerCase().includes('monogloss') ||
        tag.name.includes('单声'),
    ) ?? null
  }

  function resolvePreferredTagId(nextTags: TagDef[], selection: TagSelection) {
    if (selection === 'first') return nextTags[0]?.id ?? ''
    if (selection === 'preserve') return selectedTagId.value
    return selection.tagId
  }

  return {
    addTag,
    deleteTag,
    findMonoglossTag,
    handleTagSchemaImport,
    loadProjectTags,
    renameTag,
    selectedTag,
    selectedTagId,
    setTags,
    tags,
  }
}

function parseTagSchemaImportPayload(text: string) {
  const trimmed = text.trim()
  if (!trimmed) throw new Error('Tag schema file is empty.')

  try {
    return extractTagSchemaRecord(JSON.parse(trimmed))
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
    const records = trimmed
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        try {
          return JSON.parse(line)
        } catch {
          throw new Error(`Invalid JSONL tag schema at line ${index + 1}.`)
        }
      })
    return extractTagSchemaRecord(records)
  }
}

function extractTagSchemaRecord(payload: unknown) {
  if (Array.isArray(payload)) {
    const record = payload.find((item) => isTagSchemaRecord(item))
    if (record) return record
    throw new Error('JSONL file must include an annopilot.tag_schema.v1 record.')
  }
  if (isTagSchemaRecord(payload)) return payload
  throw new Error('Tag schema must use annopilot.tag_schema.v1 format.')
}

function isTagSchemaRecord(payload: unknown): payload is Record<string, unknown> {
  return Boolean(
    payload &&
      typeof payload === 'object' &&
      (payload as Record<string, unknown>).schema_version === 'annopilot.tag_schema.v1' &&
      (payload as Record<string, unknown>).record_type === 'tag_schema',
  )
}
