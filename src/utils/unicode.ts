/** Backend text offsets are Python code-point offsets, not JavaScript UTF-16 offsets. */
export function sliceByCodePoint(value: string, start: number, end: number) {
  return Array.from(value).slice(start, end).join('')
}
