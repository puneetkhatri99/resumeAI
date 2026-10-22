import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HealthStatus } from './HealthStatus.tsx'

const CASES = [
  {
    scenario: 'backend and database are up',
    fetchImpl: () => Promise.resolve(Response.json({ status: 'ok', database: 'ok' }, { status: 200 })),
    expectedText: 'Backend OK · Database OK',
  },
  {
    scenario: 'backend answers 503 (database down)',
    fetchImpl: () =>
      Promise.resolve(Response.json({ status: 'degraded', database: 'unavailable' }, { status: 503 })),
    expectedText: 'Backend OK · Database unavailable',
  },
  {
    scenario: 'backend is unreachable',
    fetchImpl: () => Promise.reject(new TypeError('Failed to fetch')),
    expectedText: 'Backend unreachable',
  },
]

describe('HealthStatus', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it.each(CASES)('shows "$expectedText" when $scenario', async ({ fetchImpl, expectedText }) => {
    vi.stubGlobal('fetch', vi.fn(fetchImpl))

    render(<HealthStatus />)

    expect(await screen.findByText(expectedText)).toBeInTheDocument()
  })
})
