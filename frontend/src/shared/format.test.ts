import { describe, expect, it } from 'vitest'
import { formatUsd } from './format.ts'

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
