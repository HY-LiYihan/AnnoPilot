import type { UiLabels } from '../i18n'

type MetricsLabels = UiLabels['metrics']

export function annotationImportSkipReasonSummary(
  counts: Record<string, number> | undefined,
  labels: MetricsLabels,
) {
  const reasonLabels = labels.importSkipReasonLabels as Record<string, string>
  const orderedReasons = ['no_sentence_match', 'invalid_spans', 'invalid_span', 'unknown']
  const availableCounts = counts ?? {}
  const parts = orderedReasons
    .filter((reason) => (availableCounts[reason] ?? 0) > 0)
    .map((reason) => `${reasonLabels[reason] ?? reason} ${availableCounts[reason]}`)
  const otherParts = Object.keys(availableCounts)
    .filter((reason) => !orderedReasons.includes(reason) && availableCounts[reason] > 0)
    .sort()
    .map((reason) => `${reasonLabels[reason] ?? reason} ${availableCounts[reason]}`)
  return [...parts, ...otherParts].join(' · ')
}
