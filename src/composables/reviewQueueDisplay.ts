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

export function reviewQueueRouteCounts(items: ReviewQueueItem[]) {
  return items.reduce<Record<string, number>>((counts, item) => {
    const route = item.rosetta_route || item.review_guidance?.rosetta_route || 'medium'
    counts[route] = (counts[route] ?? 0) + 1
    return counts
  }, {})
}

export function reviewQueueRouteSummary(items: ReviewQueueItem[], labels: MetricsLabels) {
  const counts = reviewQueueRouteCounts(items)
  const orderedRoutes = ['low', 'medium', 'high']
  const parts = orderedRoutes
    .filter((route) => counts[route])
    .map((route) => `${rosettaRouteLabel(route, labels)} ${counts[route]}`)
  const otherParts = Object.keys(counts)
    .filter((route) => !orderedRoutes.includes(route))
    .sort()
    .map((route) => `${rosettaRouteLabel(route, labels)} ${counts[route]}`)
  return [...parts, ...otherParts].join(' · ')
}
