import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AiUsagePanel } from './AiUsagePanel.tsx'
import type { AiUsageReport } from './api.ts'

// Synthetic data only.
const REPORT: AiUsageReport = {
  this_month: { calls: 2, tokens: 2600, cost_usd: 0.0044, unpriced_calls: 0 },
  all_time: { calls: 3, tokens: 3900, cost_usd: 0.0044, unpriced_calls: 1 },
  by_model: [
    { key: 'gpt-4.1-mini', calls: 2, tokens: 2600, cost_usd: 0.0044, unpriced_calls: 0 },
    { key: 'gpt-unknown', calls: 1, tokens: 1300, cost_usd: 0, unpriced_calls: 1 },
  ],
  by_call_type: [
    { key: 'ocr_retry', calls: 1, tokens: 1300, cost_usd: 0, unpriced_calls: 1 },
    { key: 'structuring', calls: 2, tokens: 2600, cost_usd: 0.0044, unpriced_calls: 0 },
  ],
  by_day: [{ day: '2026-10-23', calls: 3, tokens: 3900, cost_usd: 0.0044, unpriced_calls: 1 }],
  recent_calls: [
    {
      created_at: '2026-10-23T09:00:00Z',
      model: 'gpt-4.1-mini',
      call_type: 'structuring',
      tokens: 1300,
      cost_usd: 0.0036,
      resume_intake_id: 886,
      candidate_name: 'Jane Doe',
    },
    {
      created_at: '2026-10-23T08:00:00Z',
      model: 'gpt-unknown',
      call_type: 'ocr_retry',
      tokens: 1300,
      cost_usd: null,
      resume_intake_id: null,
      candidate_name: null,
    },
  ],
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('AiUsagePanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the totals of this month and all time, warning about unpriced calls', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(REPORT))))
    render(<AiUsagePanel />)

    const month = await screen.findByRole('region', { name: 'This month' })
    expect(within(month).getByText('$0.0044')).toBeInTheDocument()
    expect(within(month).getByText((2600).toLocaleString())).toBeInTheDocument()
    expect(within(month).queryByText(/unpriced/)).not.toBeInTheDocument()
    const allTime = screen.getByRole('region', { name: 'All time' })
    expect(within(allTime).getByText('$0.0044')).toBeInTheDocument()
    expect(within(allTime).getByText('+ 1 unpriced call')).toBeInTheDocument()
    expect(within(allTime).getByText('3')).toBeInTheDocument()
  })

  it('shows $0 plus a warning for an unpriced model, "no price" for an unpriced call, and links calls to interviews', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(REPORT))))
    render(<AiUsagePanel />)

    const byModel = await screen.findByRole('table', { name: 'By model' })
    const unpricedModel = within(byModel).getByRole('row', { name: /gpt-unknown/ })
    expect(unpricedModel).toHaveTextContent('$0.0000 + 1 unpriced call')
    expect(unpricedModel).not.toHaveTextContent('no price')
    expect(within(byModel).getByRole('row', { name: /gpt-4\.1-mini/ })).not.toHaveTextContent('unpriced')
    const byType = screen.getByRole('table', { name: 'By call type' })
    expect(within(byType).getByRole('rowheader', { name: 'OCR retry' })).toBeInTheDocument()
    const recent = screen.getByRole('table', { name: 'Recent AI calls' })
    expect(within(recent).getByRole('link', { name: 'Jane Doe' })).toHaveAttribute('href', '#/history/886')
    const failedUpload = within(recent).getByRole('row', { name: /gpt-unknown/ })
    expect(failedUpload).toHaveTextContent('OCR retry')
    expect(failedUpload).toHaveTextContent('no price')
    expect(within(failedUpload).queryByRole('link')).not.toBeInTheDocument()
  })

  it('sends the browser time zone, URL-encoded', async () => {
    const realOptions = new Intl.DateTimeFormat().resolvedOptions()
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({
      ...realOptions,
      timeZone: 'America/Argentina/Buenos_Aires',
    })
    const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(REPORT)))
    vi.stubGlobal('fetch', fetchMock)
    render(<AiUsagePanel />)

    await screen.findByRole('region', { name: 'This month' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai_usage?tz=America%2FArgentina%2FBuenos_Aires')
  })

  it('shows an error with "Try again", which loads the usage again', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: 'The database is unavailable. Try again later.' }, 503))
      .mockResolvedValueOnce(jsonResponse(REPORT))
    vi.stubGlobal('fetch', fetchMock)
    render(<AiUsagePanel />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the AI usage.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('region', { name: 'This month' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
