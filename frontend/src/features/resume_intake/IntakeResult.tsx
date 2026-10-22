import { useEffect, useRef } from 'react'
import { BrandedPdfLink } from '../resume_generation/BrandedPdfLink.tsx'
import { StructuredResume } from '../resume_structuring/StructuredResume.tsx'
import type { ResumeIntake } from './api.ts'
import './resumeIntake.css'

const WORK_MODE_LABELS: Record<ResumeIntake['work_mode'], string> = {
  remote: 'Remote',
  hybrid: 'Hybrid',
  onsite: 'On-site',
}

type IntakeResultProps = {
  result: ResumeIntake
  /** Called when the interviewer wants an empty form for the next interview. */
  onReset: () => void
}

/**
 * Shows a stored interview: the candidate and the details AI extracted from the CV (when the intake has them),
 * the interviewer's assessment, the CV's file name and type, and the text read from the CV. When the intake has
 * extracted details, the title band also offers "Open branded PDF" (from resume_generation).
 * Takes keyboard focus on mount, because it replaces the form and focus would otherwise fall back to the page body.
 */
export function IntakeResult({ result, onReset }: IntakeResultProps) {
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  const savedAt = new Date(result.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })

  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow hero-eyebrow-success">Interview saved</span>
            <h2 ref={headingRef} tabIndex={-1} className="hero-title">
              Interview summary
            </h2>
            <p className="hero-text">{`Saved ${savedAt} · Intake #${result.id}`}</p>
          </div>
          <div className="hero-actions">
            {result.structured_data && <BrandedPdfLink intakeId={result.id} />}
            <button type="button" className="button button-accent" onClick={onReset}>
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14" />
                <path d="M5 12h14" />
              </svg>
              New interview
            </button>
          </div>
        </div>
      </section>

      <div className="page-container page-body intake-result">
        {result.structured_data && <StructuredResume data={result.structured_data} />}

        <div className="result-aside">
          <section className="card assessment" aria-labelledby="your-assessment-heading">
            <div className="card-header">
              <span className="icon-tile tile-violet" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z" />
                </svg>
              </span>
              <h3 id="your-assessment-heading">Your assessment</h3>
            </div>
            <div className="rating-panel">
              <div>
                <span className="rating-label">Rating</span>
                <p className="rating-score">{`${result.rating} / 5`}</p>
              </div>
              <div className="rating-bar" aria-hidden="true">
                {[1, 2, 3, 4, 5].map((step) => (
                  <span key={step} className={step <= result.rating ? 'rating-bar-on' : undefined} />
                ))}
              </div>
            </div>
            <dl className="assessment-facts">
              <dt>Work mode</dt>
              <dd>
                <span className="pill pill-teal">{WORK_MODE_LABELS[result.work_mode]}</span>
              </dd>
              <dt>Notice period / start date</dt>
              <dd>{result.notice_period}</dd>
              <dt>Salary expectation</dt>
              <dd>{result.salary_expectation}</dd>
            </dl>
            <div className="assessment-notes">
              <div className="note-block note-strengths">
                <h4>Strengths</h4>
                <p>{result.strengths}</p>
              </div>
              <div className="note-block note-concerns">
                <h4>Concerns</h4>
                <p>{result.concerns}</p>
              </div>
              <div className="note-block note-overall">
                <h4>Overall notes</h4>
                <p>{result.overall_notes}</p>
              </div>
            </div>
          </section>

          <section className="card file-card" aria-label="CV file">
            <span className="icon-tile tile-blue" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                <path d="M14 3v5h5" />
              </svg>
            </span>
            <div className="file-card-text">
              <span className="file-card-name">{result.original_filename}</span>
              <span className="file-card-detail">{`${result.file_type.toUpperCase()} · text saved, file not kept`}</span>
            </div>
          </section>
        </div>

        <section className="card raw-text">
          <details open>
            <summary>
              <span className="icon-tile tile-indigo" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="#ffd08a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 6h16" />
                  <path d="M4 12h16" />
                  <path d="M4 18h10" />
                </svg>
              </span>
              <span className="raw-text-title">
                <span>Text read from the CV</span>
                <span className="raw-text-subtitle">Exactly as extracted, before AI</span>
              </span>
            </summary>
            <pre>{result.extracted_text}</pre>
          </details>
        </section>
      </div>
    </>
  )
}
