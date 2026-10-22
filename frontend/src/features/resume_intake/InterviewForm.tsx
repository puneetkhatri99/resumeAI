import { useId, useState, type FormEvent } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { submitResumeIntake, type IntakeStage, type ResumeIntake } from './api.ts'
import { CvDropzone } from './CvDropzone.tsx'
import './resumeIntake.css'

// Same limits as the backend, so the browser stops an over-long answer before the upload.
const MAX_SHORT_ANSWER_CHARS = 200
const MAX_LONG_ANSWER_CHARS = 5000
// Checked before sending because the Vite dev proxy turns the backend's early 413 into a connection reset.
const MAX_CV_BYTES = 10 * 1024 * 1024
const RATINGS = ['1', '2', '3', '4', '5']
const WORK_MODES = [
  ['remote', 'Remote'],
  ['hybrid', 'Hybrid'],
  ['onsite', 'On-site'],
]
// Field name to visible label, also used in the "Fill in <Label>." message.
const TEXT_ANSWER_LABELS = {
  notice_period: 'Notice period / start date',
  salary_expectation: 'Salary expectation',
  strengths: 'Strengths',
  concerns: 'Concerns',
  overall_notes: 'Overall notes',
}
const FILE_TOO_LARGE_MESSAGE = 'The CV is larger than 10 MB.'
const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'
// "uploading" is ours: the backend only starts, and reports "reading", once the whole upload has arrived.
const STAGE_LABELS: Record<'uploading' | IntakeStage, string> = {
  uploading: 'Submitting…',
  reading: 'Extracting text…',
  extracting: 'AI cleaning…',
}
// The steps in the progress card, in order, with how far the bar is filled while each one runs.
const PROGRESS_STEPS: { stage: keyof typeof STAGE_LABELS; label: string; hint: string; barWidth: string }[] = [
  { stage: 'uploading', label: 'Submitting', hint: 'Sending the CV and your answers', barWidth: '20%' },
  { stage: 'reading', label: 'Extracting text', hint: 'Reading the text out of the CV', barWidth: '50%' },
  { stage: 'extracting', label: 'AI cleaning', hint: 'Sorting the CV into sections · up to a minute', barWidth: '82%' },
]
// Each stage stays on screen at least this long: reading the text takes milliseconds and would otherwise flash by.
const MIN_STAGE_MS = 700

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)))

type InterviewFormProps = {
  /** Called with the stored intake once the backend accepts the submission. */
  onSubmitted: (result: ResumeIntake) => void
}

/**
 * The interview screen: the CV card plus the seven required answers (work mode and rating as radio pills that
 * start unchosen). Inputs are uncontrolled: the browser enforces `required` and `maxLength`, and the form is sent
 * as multipart FormData. Before sending, it also refuses a CV over 10 MB and an answer of only spaces
 * (both of which the browser lets through). Those errors and backend errors go in an alert next to Submit.
 * While sending, the answers are locked and a progress card covers the page: Submitting, Extracting text,
 * then AI cleaning, each shown for at least MIN_STAGE_MS.
 */
export function InterviewForm({ onSubmitted }: InterviewFormProps) {
  const [stage, setStage] = useState<keyof typeof STAGE_LABELS | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isSubmitting = stage !== null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const formData = new FormData(form)
    if ((formData.get('file') as File).size > MAX_CV_BYTES) {
      setError(FILE_TOO_LARGE_MESSAGE)
      return
    }
    const blankAnswer = Object.entries(TEXT_ANSWER_LABELS).find(([name]) => !String(formData.get(name)).trim())
    if (blankAnswer) {
      const [name, label] = blankAnswer
      setError(`Fill in ${label}.`)
      ;(form.elements.namedItem(name) as HTMLElement).focus()
      return
    }

    setStage('uploading')
    setError(null)
    // Stages are queued so each is shown for MIN_STAGE_MS; an error stops the queue and shows at once.
    let stageShownAt = Date.now()
    let stageQueue = Promise.resolve()
    let stagesStopped = false
    function showStage(next: IntakeStage) {
      stageQueue = stageQueue.then(async () => {
        await wait(MIN_STAGE_MS - (Date.now() - stageShownAt))
        if (stagesStopped) return
        setStage(next)
        stageShownAt = Date.now()
      })
    }
    try {
      const result = await submitResumeIntake(formData, showStage)
      await stageQueue
      onSubmitted(result)
    } catch (caught) {
      stagesStopped = true
      if (caught instanceof ApiError) {
        // The backend limit is still the real guard (it counts the whole request); its 413 body is plain text.
        setError(caught.status === 413 ? FILE_TOO_LARGE_MESSAGE : caught.message)
      } else {
        // Also a stream that ended without a result.
        setError(NETWORK_ERROR_MESSAGE)
      }
      setStage(null)
    }
  }

  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow">New interview</span>
            <h2 className="hero-title">Interview intake</h2>
            <p className="hero-text">Add the candidate's CV and your notes from the interview. Every field is required.</p>
          </div>
        </div>
      </section>
      <form className="page-container page-body intake-form" onSubmit={handleSubmit}>
        <div className="intake-side">
          <CvDropzone />
        </div>
        {stage && <SubmitProgress stage={stage} />}
        <fieldset className="intake-answers" disabled={isSubmitting}>
          <section className="card intake-card" aria-labelledby="availability-heading">
            <div className="card-header">
              <span className="icon-tile tile-teal" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 2" />
                </svg>
              </span>
              <div>
                <span className="step-label text-teal">Step 2</span>
                <h3 id="availability-heading">Availability &amp; pay</h3>
              </div>
            </div>
            <div className="field-row">
              <label className="field">
                <span className="field-label">{TEXT_ANSWER_LABELS.notice_period}</span>
                <input name="notice_period" required maxLength={MAX_SHORT_ANSWER_CHARS} placeholder="e.g. 4 weeks, or from 1 March" />
              </label>
              <label className="field">
                <span className="field-label">{TEXT_ANSWER_LABELS.salary_expectation}</span>
                <input name="salary_expectation" required maxLength={MAX_SHORT_ANSWER_CHARS} placeholder="e.g. $60k/year" />
              </label>
            </div>
            <fieldset className="choice-group">
              <legend className="field-label">Work mode</legend>
              {/* Nothing is checked at first, so `required` makes the interviewer choose. */}
              <div className="choices choices-teal">
                {WORK_MODES.map(([value, label]) => (
                  <label key={value} className="choice">
                    <input type="radio" name="work_mode" value={value} required />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          </section>

          <section className="card intake-card" aria-labelledby="assessment-heading">
            <div className="card-header">
              <span className="icon-tile tile-violet" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z" />
                </svg>
              </span>
              <div>
                <span className="step-label text-violet">Step 3</span>
                <h3 id="assessment-heading">Interviewer assessment</h3>
              </div>
            </div>
            <fieldset className="choice-group">
              <legend className="field-label">
                Rating <span className="field-hint">· 1 lowest, 5 highest</span>
              </legend>
              <div className="choices choices-violet">
                {RATINGS.map((rating) => (
                  <label key={rating} className="choice">
                    <input type="radio" name="rating" value={rating} required />
                    {rating}
                  </label>
                ))}
              </div>
            </fieldset>
            <LongAnswerField name="strengths" />
            <LongAnswerField name="concerns" />
            <LongAnswerField name="overall_notes" />
          </section>

          <div className="intake-actions">
            {error && (
              <p role="alert" className="alert alert-error">
                {error}
              </p>
            )}
            <p className="intake-actions-text">
              {isSubmitting
                ? 'Keep this tab open. Your answers are locked until the CV is processed.'
                : 'Submitting reads the CV and extracts its details with AI. This can take up to a minute.'}
            </p>
            <button type="submit" className="button button-primary" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <span className="button-spinner" aria-hidden="true" />
                  Submitting…
                </>
              ) : (
                <>
                  Submit
                  <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14" />
                    <path d="m13 6 6 6-6 6" />
                  </svg>
                </>
              )}
            </button>
          </div>
        </fieldset>
      </form>
    </>
  )
}

/** A required long answer with a live "used / 5,000" character count under it. */
function LongAnswerField({ name }: { name: 'strengths' | 'concerns' | 'overall_notes' }) {
  const id = useId()
  const [length, setLength] = useState(0)

  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {TEXT_ANSWER_LABELS[name]}
      </label>
      <textarea
        id={id}
        name={name}
        required
        maxLength={MAX_LONG_ANSWER_CHARS}
        rows={4}
        onChange={(event) => setLength(event.currentTarget.value.length)}
      />
      <span className="field-counter">{`${length.toLocaleString('en-US')} / ${MAX_LONG_ANSWER_CHARS.toLocaleString('en-US')}`}</span>
    </div>
  )
}

/**
 * The progress card shown over the whole page while sending (wherever the interviewer has scrolled to):
 * a bar, the three steps and the current stage.
 */
function SubmitProgress({ stage }: { stage: keyof typeof STAGE_LABELS }) {
  const current = PROGRESS_STEPS.findIndex((step) => step.stage === stage)

  return (
    <div className="progress-overlay">
      <section className="card progress-card" aria-labelledby="progress-heading">
        <div className="progress-bar" aria-hidden="true">
          <span style={{ width: PROGRESS_STEPS[current].barWidth }} />
        </div>
        <div className="progress-body">
          <div className="progress-head">
            <h3 id="progress-heading">Processing the interview</h3>
            <span className="progress-count">{`Step ${current + 1} of ${PROGRESS_STEPS.length}`}</span>
          </div>
          <ol className="progress-steps">
            {PROGRESS_STEPS.map((step, index) => (
              <li
                key={step.stage}
                className={`progress-step progress-step-${index < current ? 'done' : index === current ? 'active' : 'todo'}`}
              >
                <span className="progress-step-label">{step.label}</span>
                <span className="progress-step-hint">{step.hint}</span>
              </li>
            ))}
          </ol>
          <p role="status" className="progress-status">
            {STAGE_LABELS[stage]}
          </p>
        </div>
      </section>
    </div>
  )
}
