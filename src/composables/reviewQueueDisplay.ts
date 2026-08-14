import type { UiLabels } from '../i18n'
import type { ReviewQueueItem } from '../types/domain'

type MetricsLabels = UiLabels['metrics']

export function rosettaRouteLabel(route: string | undefined, labels: MetricsLabels) {
  const normalized = route || 'medium'
  const routeLabels = labels.rosettaRouteLabels as Record<string, string>
  return routeLabels[normalized] ?? normalized
}

export function reviewQueuePriorityRouteText(item: ReviewQueueItem, labels: MetricsLabels) {
  const route = item.rosetta_route || item.review_guidance?.rosetta_route || 'medium'
  return `${labels.priority} ${item.priority} · ${labels.rosettaRoute} ${rosettaRouteLabel(route, labels)}`
}
