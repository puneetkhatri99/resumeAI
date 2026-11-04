import { describe, expect, it } from 'vitest'
import { formatUsd, formatWhen } from './format.ts'

describe('formatUsd', () => {
  it.each([
    [0.0044, '$0.0044'],
    [0.00082, '$0.0008'],
    [0, '$0.0000'],
    [12.3456, '$12.3456'],
    [null, '—'],
  ])('formats %s as "%s"', (cost, expected) => {
    expect(formatUsd(cost)).toBe(expected)
  })
})

describe('formatWhen', () => {
  const now = new Date(2026, 10, 4, 21, 0)
  const at = (...parts: [number, number, number, number?, number?]) => new Date(...parts).toISOString()
  const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { timeStyle: 'short' })

  it('says Today and Yesterday with the time', () => {
    const today = at(2026, 10, 4, 0, 5)
    const yesterday = at(2026, 10, 3, 23, 55)
    expect(formatWhen(today, now)).toBe(`Today, ${time(today)}`)
    expect(formatWhen(yesterday, now)).toBe(`Yesterday, ${time(yesterday)}`)
  })

  it('drops the year this year and keeps it for earlier years', () => {
    expect(formatWhen(at(2026, 10, 2, 9), now)).toBe(new Date(2026, 10, 2).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }))
    expect(formatWhen(at(2025, 10, 3, 9), now)).toBe(new Date(2025, 10, 3).toLocaleDateString(undefined, { dateStyle: 'medium' }))
  })
})
