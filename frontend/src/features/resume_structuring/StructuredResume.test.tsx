import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StructuredResume, type ResumeData } from './StructuredResume.tsx'

// Synthetic candidate only: no real person's data in tests.
const FULL_DATA: ResumeData = {
  name: 'Jane Doe',
  email: 'jane.doe@example.com',
  phone: '+1 555 0100',
  location: 'Springfield, USA',
  links: ['https://github.com/janedoe'],
  skills: ['Python', 'SQL'],
  experience: [
    {
      company: 'Acme Corp',
      title: 'Software engineer',
      start_date: 'Jan 2020',
      end_date: 'Present',
      location: 'Remote',
      description: 'Builds APIs.',
    },
  ],
  education: [
    {
      institution: 'State University',
      degree: 'BSc',
      field_of_study: 'Computer Science',
      start_date: '2015',
      end_date: '2019',
    },
  ],
  projects: [
    { name: 'Resume parser', description: 'Parses CVs.', link: 'https://example.com/parser', technologies: ['FastAPI', 'React'] },
  ],
  certificates: [{ name: 'AWS Cloud Practitioner', issuer: 'AWS', date: '2022' }],
}

const EMPTY_DATA: ResumeData = {
  name: 'Jane Doe',
  email: 'jane.doe@example.com',
  phone: null,
  location: null,
  links: [],
  skills: [],
  experience: [],
  education: [],
  projects: [],
  certificates: [],
}

function headingTexts() {
  return screen.getAllByRole('heading').map((heading) => heading.textContent)
}

describe('StructuredResume', () => {
  it('shows every section with its details', () => {
    render(<StructuredResume data={FULL_DATA} />)

    expect(headingTexts()).toEqual(['Contact', 'Links', 'Skills', 'Experience', 'Education', 'Projects', 'Certificates'])
    for (const text of [
      'Jane Doe',
      'jane.doe@example.com',
      '+1 555 0100',
      'https://github.com/janedoe',
      'Python',
      'SQL',
      'Software engineer · Acme Corp',
      'Jan 2020 – Present · Remote',
      'Builds APIs.',
      'BSc, Computer Science',
      'State University · 2015 – 2019',
      'Resume parser',
      'FastAPI, React',
      'Parses CVs.',
      'https://example.com/parser',
      'AWS Cloud Practitioner · AWS · 2022',
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument()
    }
    // The location pill, with its screen-reader prefix like the email and phone pills.
    expect(screen.getByText('Springfield, USA').textContent).toBe('Location: Springfield, USA')
  })

  it('leaves out empty sections, entries with nothing in them and null fields', () => {
    const data: ResumeData = {
      ...EMPTY_DATA,
      experience: [{ company: 'Acme Corp', title: null, start_date: 'Jan 2020', end_date: null, location: null, description: null }],
      certificates: [{ name: null, issuer: null, date: null }],
    }

    render(<StructuredResume data={data} />)

    expect(headingTexts()).toEqual(['Contact', 'Experience'])
    expect(screen.queryByText('Phone')).not.toBeInTheDocument()
    const lines = [...screen.getByRole('listitem').children].map((line) => line.textContent)
    expect(lines).toEqual(['Acme Corp', 'Jan 2020'])
  })

  it('makes only http(s) links clickable, opening in a new tab', () => {
    const data: ResumeData = { ...EMPTY_DATA, links: ['https://github.com/janedoe', 'javascript:alert(1)'] }

    render(<StructuredResume data={data} />)

    const link = screen.getByRole('link', { name: 'https://github.com/janedoe' })
    expect(link).toHaveAttribute('href', 'https://github.com/janedoe')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('javascript:alert(1)')).toBeInTheDocument()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })
})
