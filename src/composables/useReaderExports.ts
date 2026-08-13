import type { Ref } from 'vue'
import {
  documentExportUrl,
  eventsExportUrl,
  goldsmithBoundaryFeedbackExportUrl,
  goldsmithCandidateRunsExportUrl,
  goldsmithConsistencyScoresExportUrl,
  goldsmithHardExamplesExportUrl,
  goldsmithHumanChoicesExportUrl,
  goldsmithRiskReasonsExportUrl,
  goldsmithReviewQueueExportUrl,
  manifestExportUrl,
  prodigyBundleExportUrl,
  prodigyExportUrl,
  prodigyLabelsExportUrl,
  prodigySpansExportUrl,
  tagSchemaExportUrl,
} from '../api/documents'
import { runProvenanceExportUrl } from '../api/runs'
import { PROJECT_ID, type DocumentMeta, type ReviewQueueOrder } from '../types/domain'

type ReaderExportOptions = {
  documentMeta: Ref<DocumentMeta | null>
  reviewQueueOrder: Ref<ReviewQueueOrder>
  onEventsExport?: () => void | Promise<void>
}

export function useReaderExports(options: ReaderExportOptions) {
  function withDocument(exportUrl: (projectId: string, documentId: string) => string) {
    if (!options.documentMeta.value) return
    window.location.href = exportUrl(PROJECT_ID, options.documentMeta.value.id)
  }

  function exportJsonl() {
    withDocument(documentExportUrl)
  }

  function exportProdigyJsonl() {
    withDocument(prodigyExportUrl)
  }

  function exportProdigyBundleZip() {
    withDocument(prodigyBundleExportUrl)
  }

  function exportProdigySpansJsonl() {
    withDocument(prodigySpansExportUrl)
  }

  function exportProdigyLabelsJson() {
    window.location.href = prodigyLabelsExportUrl(PROJECT_ID)
  }

  function exportManifestJson() {
    withDocument(manifestExportUrl)
  }

  function exportEventsJsonl() {
    window.location.href = eventsExportUrl(PROJECT_ID)
    void options.onEventsExport?.()
  }

  function exportTagSchemaJson() {
    window.location.href = tagSchemaExportUrl(PROJECT_ID)
  }

  function exportRunProvenanceJson(runId: string) {
    window.location.href = runProvenanceExportUrl(PROJECT_ID, runId)
  }

  function exportGoldsmithReviewQueueJsonl() {
    if (!options.documentMeta.value) return
    window.location.href = goldsmithReviewQueueExportUrl(PROJECT_ID, options.documentMeta.value.id, options.reviewQueueOrder.value)
  }

  function exportGoldsmithHumanChoicesJsonl() {
    withDocument(goldsmithHumanChoicesExportUrl)
  }

  function exportGoldsmithHardExamplesJsonl() {
    withDocument(goldsmithHardExamplesExportUrl)
  }

  function exportGoldsmithBoundaryFeedbackJsonl() {
    withDocument(goldsmithBoundaryFeedbackExportUrl)
  }

  function exportGoldsmithConsistencyScoresJsonl() {
    withDocument(goldsmithConsistencyScoresExportUrl)
  }

  function exportGoldsmithCandidateRunsJsonl() {
    withDocument(goldsmithCandidateRunsExportUrl)
  }

  function exportGoldsmithRiskReasonsJsonl() {
    withDocument(goldsmithRiskReasonsExportUrl)
  }

  return {
    exportEventsJsonl,
    exportGoldsmithBoundaryFeedbackJsonl,
    exportGoldsmithCandidateRunsJsonl,
    exportGoldsmithConsistencyScoresJsonl,
    exportGoldsmithHardExamplesJsonl,
    exportGoldsmithHumanChoicesJsonl,
    exportGoldsmithRiskReasonsJsonl,
    exportGoldsmithReviewQueueJsonl,
    exportJsonl,
    exportManifestJson,
    exportProdigyBundleZip,
    exportProdigyLabelsJson,
    exportProdigyJsonl,
    exportProdigySpansJsonl,
    exportRunProvenanceJson,
    exportTagSchemaJson,
  }
}
