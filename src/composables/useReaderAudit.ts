import { ref, type Ref } from 'vue'
import { fetchAnnotationImports, fetchAuditSummary, previewRebuild } from '../api/audit'
import { importAnnotationsJsonl } from '../api/documents'
import { fetchRuns } from '../api/runs'
import {
  PROJECT_ID,
  type AnnotationImportSummary,
  type AnnotationRun,
  type AuditSummary,
  type DocumentMeta,
  type RebuildPreview,
} from '../types/domain'

type UseReaderAuditOptions = {
  documentMeta: Ref<DocumentMeta | null>
  isUploading: Ref<boolean>
  loadDocument: (documentId: string, preserveCurrent?: boolean) => Promise<void>
  readerError: Ref<string>
}

export function useReaderAudit(options: UseReaderAuditOptions) {
  const auditSummary = ref<AuditSummary | null>(null)
  const rebuildPreview = ref<RebuildPreview | null>(null)
  const runHistory = ref<AnnotationRun[]>([])
  const lastAnnotationImport = ref<AnnotationImportSummary | null>(null)
  const isVerifyingRebuild = ref(false)

  async function handleAnnotationImport(file: File) {
    const documentId = options.documentMeta.value?.id
    if (!documentId || options.isUploading.value) return
    options.isUploading.value = true
    options.readerError.value = ''
    try {
      const imported = await importAnnotationsJsonl(PROJECT_ID, documentId, file)
      await options.loadDocument(documentId, true)
      await refreshAuditSummary()
      await refreshRunHistory()
      lastAnnotationImport.value = { ...imported, import_filename: file.name }
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not import annotation JSONL.'
    } finally {
      options.isUploading.value = false
    }
  }

  async function refreshAuditSummary() {
    try {
      auditSummary.value = await fetchAuditSummary(PROJECT_ID)
    } catch {
      auditSummary.value = null
    }
  }

  async function refreshAnnotationImportHistory(documentId: string) {
    try {
      const payload = await fetchAnnotationImports(PROJECT_ID, documentId, 1)
      const latestImport = payload.imports[0]
      lastAnnotationImport.value = latestImport ? { ...latestImport, import_filename: latestImport.import_filename || latestImport.filename } : null
    } catch {
      lastAnnotationImport.value = null
    }
  }

  async function verifyRebuildPreview() {
    if (isVerifyingRebuild.value) return
    isVerifyingRebuild.value = true
    options.readerError.value = ''
    try {
      rebuildPreview.value = await previewRebuild(PROJECT_ID)
      await refreshAuditSummary()
    } catch (error) {
      options.readerError.value = error instanceof Error ? error.message : 'Could not verify rebuild.'
    } finally {
      isVerifyingRebuild.value = false
    }
  }

  async function refreshRunHistory() {
    try {
      const payload = await fetchRuns(PROJECT_ID, options.documentMeta.value?.id, 5)
      runHistory.value = payload.runs
    } catch {
      runHistory.value = []
    }
  }

  function resetAuditState() {
    auditSummary.value = null
    rebuildPreview.value = null
    runHistory.value = []
    lastAnnotationImport.value = null
  }

  return {
    auditSummary,
    handleAnnotationImport,
    isVerifyingRebuild,
    lastAnnotationImport,
    rebuildPreview,
    refreshAnnotationImportHistory,
    refreshAuditSummary,
    refreshRunHistory,
    resetAuditState,
    runHistory,
    verifyRebuildPreview,
  }
}
