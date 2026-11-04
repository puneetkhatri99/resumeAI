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
  /** The CV's own summary as written, or 2–3 sentences the AI wrote from the CV's facts. Absent on old fixtures. */
  summary?: string | null
  links: string[]
  /** The skills the CV lists. */
  skills: string[]
  /** Skills the CV shows the candidate used but doesn't list, each with a quote from the CV. Absent on old fixtures. */
  inferred_skills?: { name: string; evidence: string }[]
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
 * readers) and headed sections: Summary, Links, Skills (as chips, the listed ones then the inferred ones, unmarked),
 * Experience, Education, Projects and Certificates, each with its own colour and icon. Empty sections and null fields are left out. Only http(s) links are clickable
 * (they open in a new tab), because they come from AI output.
 */
export function StructuredResume({ data }: StructuredResumeProps) {
  const skills = [...data.skills, ...(data.inferred_skills ?? []).map((skill) => skill.name)]
  // Each section is a list of entries, and each entry is a list of lines.
  const sections: [string, ReactNode[][]][] = [
    ['Summary', data.summary ? [[data.summary]] : []],
    ['Links', data.links.map((link) => [linkOrText(link)])],
    ['Skills', skills.length > 0 ? [[skills.join(', ')]] : []],
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
            <section key={title} className="cv-section">
              <div className="cv-section-header">
                <h3>{title}</h3>
              </div>
              {title === 'Skills' ? (
                <ul className="skill-chips">
                  {skills.map((skill, index) => (
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

