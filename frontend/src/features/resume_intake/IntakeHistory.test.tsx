import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { IntakeSummary } from './api.ts'
import { IntakeHistory } from './IntakeHistory.tsx'

// Synthetic candidates only.
const ROW: IntakeSummary = {
  id: 359,
  created_at: '2026-10-22T12:39:41Z',
  original_filename: 'jane_doe_cv.pdf',
  candidate_name: 'Jane Doe',
  candidate_email: 'jane.doe@example.com',
  rating: 4,
  work_mode: 'onsite',
}
// Stored before AI extraction existed: no name or email.
const OLD_ROW: IntakeSummary = {
  ...ROW,
  id: 12,
  original_filename: 'old_cv.txt',
  candidate_name: null,
  candidate_email: null,
  rating: 2,
  work_mode: 'remote',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('IntakeHistory', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists each interview with name, email, date, rating and work mode, linking to its result', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ items: [ROW, OLD_ROW], has_more: false }))))
    render(<IntakeHistory />)

    const row = await screen.findByRole('link', { name: /Jane Doe/ })
    expect(row).toHaveAttribute('href', '#/history/359')
    const date = new Date(ROW.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    for (const text of ['jane.doe@example.com', date, '4 / 5', 'On-site']) {
      expect(within(row).getByText(text)).toBeInTheDocument()
    }
    // No AI data: the file name stands in for the name.
    expect(screen.getByRole('link', { name: /old_cv\.txt/ })).toHaveAttribute('href', '#/history/12')
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('"Load more" asks for the interviews older than the last one shown and adds them below', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: true }))
      .mockResolvedValueOnce(jsonResponse({ items: [OLD_ROW], has_more: false }))
    vi.stubGlobal('fetch', fetchMock)
    render(<IntakeHistory />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('link', { name: /old_cv\.txt/ })).toBeInTheDocument()
    expect(screen.getAllByRole('link')).toHaveLength(2)
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/resume_intake', '/api/resume_intake?before_id=359'])
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('says so when there are no interviews yet', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ items: [], has_more: false }))))
    render(<IntakeHistory />)

    expect(await screen.findByText(/No interviews yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a new interview' })).toHaveAttribute('href', '#/')
  })

  it('shows an error with "Try again", which loads the list again', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'The database is unavailable. Try again later.' }, 503))
      .mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: false }))
    vi.stubGlobal('fetch', fetchMock)
    render(<IntakeHistory />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load past interviews.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('link', { name: /Jane Doe/ })).toBeInTheDocument()
    expect(fetchMock.mock.calls[1][0]).toBe('/api/resume_intake')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
