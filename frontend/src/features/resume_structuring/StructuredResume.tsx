import type { ReactNode } from 'react'
import './structuredResume.css'

/**
 * The details AI extracted from a CV. Mirrors ResumeData in backend/app/features/resume_structuring/schemas.py:
 * anything the CV doesn't state is null or an empty list, and dates are kept as written in the CV.
 */
export type ResumeData = {
  name: string
  email: string
  phone: string | null
  /** The candidate's current location, as written in the CV. */
  location: string | null
  links: string[]
  skills: string[]
  experience: {
    company: string | null
    title: string | null
    start_date: string | null
    end_date: string | null
    location: string | null
    description: string | null
  }[]
  education: {
    institution: string | null
    degree: string | null
    field_of_study: string | null
    start_date: string | null
    end_date: string | null
  }[]
  projects: {
    name: string | null
    description: string | null
    link: string | null
    technologies: string[]
  }[]
  certificates: {
    name: string | null
    issuer: string | null
    date: string | null
  }[]
}

type StructuredResumeProps = {
  data: ResumeData
}

/** Joins the parts that are present (not null or empty), e.g. a title and a company, or two dates. */
function joinPresent(parts: (string | null)[], separator = ' · '): string {
  return parts.filter((part) => part).join(separator)
}

/** A URL from AI output: clickable only when it is http(s), so e.g. a `javascript:` URL stays plain text. */
function linkOrText(url: string | null): ReactNode {
  return url && /^https?:\/\//i.test(url) ? (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {url}
    </a>
  ) : (
    url
  )
}

/**
 * Shows extracted CV details as two cards: the candidate (name, email, phone, location, under a "Contact" heading for screen
 * readers) and headed sections: Links, Skills (as chips), Experience, Education, Projects and Certificates, each
 * with its own colour and icon. Empty sections and null fields are left out. Only http(s) links are clickable
 * (they open in a new tab), because they come from AI output.
 */
export function StructuredResume({ data }: StructuredResumeProps) {
  // Each section is a list of entries, and each entry is a list of lines.
  const sections: [string, ReactNode[][]][] = [
    ['Links', data.links.map((link) => [linkOrText(link)])],
    ['Skills', data.skills.length > 0 ? [[data.skills.join(', ')]] : []],
    [
      'Experience',
      data.experience.map((job) => [
        joinPresent([job.title, job.company]),
        joinPresent([joinPresent([job.start_date, job.end_date], ' – '), job.location]),
        job.description ?? '',
      ]),
    ],
    [
      'Education',
      data.education.map((school) => [
        joinPresent([school.degree, school.field_of_study], ', '),
        joinPresent([school.institution, joinPresent([school.start_date, school.end_date], ' – ')]),
      ]),
    ],
    [
      'Projects',
      data.projects.map((project) => [
        project.name ?? '',
        project.technologies.join(', '),
        project.description ?? '',
        linkOrText(project.link),
      ]),
    ],
    ['Certificates', data.certificates.map((certificate) => [joinPresent([certificate.name, certificate.issuer, certificate.date])])],
  ]

  const shownSections = sections
    .map(([title, entries]) => ({
      title,
      entries: entries.map((lines) => lines.filter((line) => line)).filter((lines) => lines.length > 0),
    }))
    .filter(({ entries }) => entries.length > 0)
  const initials = data.name
    .split(/\s+/)
    .filter((word) => word)
    .slice(0, 2)
    .map((word) => [...word][0].toUpperCase())
    .join('')

  return (
    <>
      <section className="card candidate-card" aria-label="Candidate">
        <span className="candidate-avatar" aria-hidden="true">
          {initials}
        </span>
        <div className="candidate-main">
          <h3 className="sr-only">Contact</h3>
          <p className="candidate-name">{data.name}</p>
          <p className="contact-pills">
            <span className="pill pill-blue">
              <span className="sr-only">Email: </span>
              {data.email}
            </span>
            {data.phone && (
              <span className="pill pill-teal">
                <span className="sr-only">Phone: </span>
                {data.phone}
              </span>
            )}
            {data.location && (
              <span className="pill pill-violet">
                <span className="sr-only">Location: </span>
                {data.location}
              </span>
            )}
          </p>
        </div>
        <p className="ai-note">Details below were extracted by AI. Check them against the CV text.</p>
      </section>
      {shownSections.length > 0 && (
        <section className="card cv-details" aria-label="Details from the CV">
          {shownSections.map(({ title, entries }) => (
            <section key={title} className={`cv-section cv-section-${SECTION_TONES[title]}`}>
              <div className="cv-section-header">
                <span className={`icon-tile tile-${SECTION_TONES[title]}`} aria-hidden="true">
                  {SECTION_ICONS[title]}
                </span>
                <h3>{title}</h3>
              </div>
              {title === 'Skills' ? (
                <ul className="skill-chips">
                  {data.skills.map((skill, index) => (
                    <li key={index}>{skill}</li>
                  ))}
                </ul>
              ) : (
                <ul className="cv-entries">
                  {entries.map((lines, entryIndex) => (
                    <li key={entryIndex}>
                      {lines.map((line, lineIndex) => (
                        <div key={lineIndex}>{line}</div>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </section>
      )}
    </>
  )
}

// Each section's colour and icon on the result screen.
const SECTION_TONES: Record<string, string> = {
  Links: 'indigo',
  Skills: 'pink',
  Experience: 'blue',
  Education: 'teal',
  Projects: 'violet',
  Certificates: 'orange',
}

const iconProps = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

const SECTION_ICONS: Record<string, ReactNode> = {
  Links: (
    <svg {...iconProps}>
      <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
    </svg>
  ),
  Skills: (
    <svg {...iconProps}>
      <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" />
      <circle cx="7.5" cy="7.5" r="1.5" />
    </svg>
  ),
  Experience: (
    <svg {...iconProps}>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  ),
  Education: (
    <svg {...iconProps}>
      <path d="m2 9 10-5 10 5-10 5z" />
      <path d="M6 11v5c3 2 9 2 12 0v-5" />
    </svg>
  ),
  Projects: (
    <svg {...iconProps}>
      <path d="m8 8-4 4 4 4" />
      <path d="m16 8 4 4-4 4" />
    </svg>
  ),
  Certificates: (
    <svg {...iconProps}>
      <circle cx="12" cy="9" r="6" />
      <path d="m8.5 14-1.5 7 5-3 5 3-1.5-7" />
    </svg>
  ),
}
