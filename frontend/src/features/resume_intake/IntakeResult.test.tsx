import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ResumeIntake } from './api.ts'
import { IntakeResult } from './IntakeResult.tsx'

const RESULT: ResumeIntake = {
  id: 1,
  original_filename: 'cv.pdf',
  file_type: 'pdf',
  extracted_text: 'Jane Doe\nSenior Python developer',
  structured_data: {
    name: 'Jane Doe',
    email: 'jane.doe@example.com',
    phone: null,
    location: null,
    links: [],
    skills: ['Python'],
    experience: [
      {
        company: 'Acme Corp',
        title: 'Senior Python developer',
        start_date: 'Jan 2020',
        end_date: 'Present',
        location: null,
        description: null,
      },
    ],
    education: [],
    projects: [],
    certificates: [],
  },
  notice_period: 'One month',
  salary_expectation: '$60k/year',
  work_mode: 'onsite',
  rating: 3,
  strengths: 'Clear communicator',
  concerns: 'Little cloud experience',
  overall_notes: 'Invite to the second round',
  created_at: '2026-10-22T10:00:00Z',
}

describe('IntakeResult', () => {
  it('shows the answers with readable labels, the CV file, its extracted details and its text', () => {
    render(<IntakeResult result={RESULT} onReset={() => {}} />)

    for (const text of [
      'One month',
      '$60k/year',
      'On-site',
      '3 / 5',
      'Clear communicator',
      'Little cloud experience',
      'Invite to the second round',
      'cv.pdf',
      'PDF · text saved, file not kept',
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
    expect(screen.getByRole('heading', { name: 'Experience' })).toBeInTheDocument()
    expect(screen.getByText('Senior Python developer · Acme Corp')).toBeInTheDocument()
    // textContent, not getByText, so the line break in the CV text is checked too.
    expect(screen.getByText(/Jane Doe Senior Python developer/).textContent).toBe(RESULT.extracted_text)
  })

  it('moves keyboard focus to its heading when shown', () => {
    render(<IntakeResult result={RESULT} onReset={() => {}} />)

    expect(screen.getByRole('heading', { name: 'Interview summary' })).toHaveFocus()
  })

  it('calls onReset when "New interview" is clicked', async () => {
    const onReset = vi.fn()
    render(<IntakeResult result={RESULT} onReset={onReset} />)

    await userEvent.setup().click(screen.getByRole('button', { name: 'New interview' }))

    expect(onReset).toHaveBeenCalledOnce()
  })

  it('links to the branded PDF in a new tab when the intake has extracted details', () => {
    render(<IntakeResult result={RESULT} onReset={() => {}} />)

    const link = screen.getByRole('link', { name: 'Open branded PDF' })
    expect(link).toHaveAttribute('href', '/api/resume_generation/1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('offers no branded PDF when the intake has no extracted details', () => {
    render(<IntakeResult result={{ ...RESULT, structured_data: null }} onReset={() => {}} />)

    expect(screen.queryByRole('link', { name: 'Open branded PDF' })).not.toBeInTheDocument()
  })
})
