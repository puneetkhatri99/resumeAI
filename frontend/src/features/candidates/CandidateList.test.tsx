import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatWhen } from '../../shared/format.ts'
import type { CandidateSummary } from './api.ts'
import { CandidateList } from './CandidateList.tsx'

// Synthetic candidates only.
const ROW: CandidateSummary = {
  id: 7,
  name: 'Jane Doe',
  created_at: '2026-11-03T09:15:00Z',
  source: 'manual',
  ai_cost_usd: 0.0012,
  original_resume_url: 'https://storage.example.com/originals/candidates/a/original.pdf?X-Amz-Signature=x',
  new_resume_url: 'https://storage.example.com/resumes/candidates/a/resume.pdf',
}
// Sent to the public API, with every AI call unpriced.
const API_ROW: CandidateSummary = {
  ...ROW,
  id: 3,
  name: 'John Roe',
  source: 'api',
  ai_cost_usd: null,
  original_resume_url: 'https://storage.example.com/originals/candidates/b/original.docx?X-Amz-Signature=y',
  new_resume_url: 'https://storage.example.com/resumes/candidates/b/resume.pdf',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** The table row whose header cell is the candidate's name. */
function rowOf(name: string): HTMLElement {
  return screen.getByRole('rowheader', { name }).closest('tr')!
}

describe('CandidateList', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows every column, with both resumes opening in a new tab and "—" for a null cost', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ items: [ROW, API_ROW], has_more: false }))))
    render(<CandidateList />)

    await screen.findByRole('rowheader', { name: 'Jane Doe' })
    const row = within(rowOf('Jane Doe'))
    const date = formatWhen(ROW.created_at)
    for (const text of [date, 'Manual', '$0.0012']) {
      expect(row.getByText(text)).toBeInTheDocument()
    }
    for (const [name, href] of [
      ['Original', ROW.original_resume_url],
      ['New resume', ROW.new_resume_url],
    ]) {
      const link = row.getByRole('link', { name })
      expect(link).toHaveAttribute('href', href)
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    }
    const apiRow = within(rowOf('John Roe'))
    expect(apiRow.getByText('API')).toBeInTheDocument()
    expect(apiRow.getByText('—')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('"Load more" asks for the candidates older than the last one shown and adds them below', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: true }))
      .mockResolvedValueOnce(jsonResponse({ items: [API_ROW], has_more: false }))
    vi.stubGlobal('fetch', fetchMock)
    render(<CandidateList />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('rowheader', { name: 'John Roe' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/candidates', '/api/candidates?before_id=7'])
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('says so when there are no candidates yet', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ items: [], has_more: false }))))
    render(<CandidateList />)

    expect(await screen.findByText(/No candidates yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add a candidate' })).toHaveAttribute('href', '#/candidates/new')
  })

  it('Delete asks first, then deletes the candidate and removes its row', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [ROW, API_ROW], has_more: false }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<CandidateList />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Delete Jane Doe' }))

    expect(confirm).toHaveBeenCalledWith('Delete this candidate and both resume files?')
    await vi.waitFor(() => expect(screen.queryByRole('rowheader', { name: 'Jane Doe' })).not.toBeInTheDocument())
    expect(fetchMock.mock.calls[1]).toEqual(['/api/candidates/7', { method: 'DELETE' }])
    expect(screen.getByRole('rowheader', { name: 'John Roe' })).toBeInTheDocument()
  })

  it('a cancelled confirm deletes nothing', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: false }))
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<CandidateList />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Delete Jane Doe' }))

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('rowheader', { name: 'Jane Doe' })).toBeInTheDocument()
  })

  it('a failed delete shows the backend message and keeps the row', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: false }))
        .mockResolvedValueOnce(jsonResponse({ detail: 'Could not delete the resume files. Try again.' }, 502)),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<CandidateList />)

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Delete Jane Doe' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not delete the resume files. Try again.')
    expect(screen.getByRole('rowheader', { name: 'Jane Doe' })).toBeInTheDocument()
  })

  it('shows an error with "Try again", which loads the list again', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockRejectedValueOnce(new TypeError('Failed to fetch'))
        .mockResolvedValueOnce(jsonResponse({ items: [ROW], has_more: false })),
    )
    render(<CandidateList />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the candidates.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('rowheader', { name: 'Jane Doe' })).toBeInTheDocument()
  })
})
