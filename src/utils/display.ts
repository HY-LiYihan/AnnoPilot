export function truncateMiddle(value: string, maxLength: number, tailLength = 10) {
  if (value.length <= maxLength) return value

  const ellipsis = '...'
  const minimumHeadLength = 6
  const availableLength = maxLength - ellipsis.length
  if (availableLength <= minimumHeadLength) return `${value.slice(0, Math.max(maxLength - ellipsis.length, 1))}${ellipsis}`

  const safeTailLength = Math.min(Math.max(tailLength, 4), availableLength - minimumHeadLength)
  const headLength = availableLength - safeTailLength
  return `${value.slice(0, headLength)}${ellipsis}${value.slice(-safeTailLength)}`
}

export function truncateFilename(value: string, maxLength = 34) {
  return truncateMiddle(value, maxLength, 12)
}
