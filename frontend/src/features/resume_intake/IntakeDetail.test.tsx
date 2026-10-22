import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ResumeIntake } from './api.ts'
import { IntakeDetail } from './IntakeDetail.tsx'

// Synthetic intake. No AI data, so the test doesn't depend on the ResumeData fields.
const INTAKE: ResumeIntake = {
  id: 7,
  original_filename: 'cv.txt',
  file_type: 'txt',
  extracted_text: 'Jane Doe\nPython developer',
  structured_data: null,
  notice_period: 'One month',
  salary_expectation: '$60k/year',
  work_mode: 'hybrid',
  rating: 4,
  strengths: 'Clear communicator',
  concerns: 'Little cloud experience',
  overall_notes: 'Invite to the second round',
  created_at: '2026-10-22T10:00:00Z',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('IntakeDetail', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the result it was given (right after a submit) without fetching it', () => {
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal('fetch', fetchMock)

    render(<IntakeDetail intakeId={7} initialResult={INTAKE} onReset={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Interview summary' })).toBeInTheDocument()
    expect(screen.getByText('Clear communicator')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('fetches the intake when it has no copy (opened from the list or a bookmark)', async () => {
    const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse(INTAKE)))
    vi.stubGlobal('fetch', fetchMock)

    render(<IntakeDetail intakeId={7} initialResult={null} onReset={() => {}} />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
    expect(await screen.findByText('Clear communicator')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/resume_intake/7')
  })

  it('says the interview does not exist on a 404, with a link back to the list', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ detail: 'Intake not found.' }, 404))))

    // The result it holds is for another intake, so it fetches this one.
    render(<IntakeDetail intakeId={999} initialResult={INTAKE} onReset={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent("This interview doesn't exist.")
    expect(screen.getByRole('link', { name: 'Back to past interviews' })).toHaveAttribute('href', '#/history')
  })
})
