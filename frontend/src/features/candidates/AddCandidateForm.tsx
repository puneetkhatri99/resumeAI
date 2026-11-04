import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { CvDropzone } from '../resume_intake/CvDropzone.tsx'
import { addCandidate, type CandidateStage } from './api.ts'
import './candidates.css'

// Checked before sending because the Vite dev proxy turns the backend's early 413 into a connection reset.
const MAX_CV_BYTES = 10 * 1024 * 1024
const FILE_TOO_LARGE_MESSAGE = 'The CV is larger than 10 MB.'
const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'
const STAGE_LABELS: Record<CandidateStage, string> = {
  reading: 'Extracting text…',
  extracting: 'AI cleaning…',
  generating: 'Creating resume…',
  uploading: 'Uploading…',
}
// The steps in the progress card, in order, with how far the bar is filled while each one runs.
const PROGRESS_STEPS: { stage: CandidateStage; label: string; hint: string; barWidth: string }[] = [
  { stage: 'reading', label: 'Extracting text', hint: 'Reading the text out of the CV', barWidth: '20%' },
  { stage: 'extracting', label: 'AI cleaning', hint: 'Sorting the CV into sections · up to a minute', barWidth: '50%' },
  { stage: 'generating', label: 'Creating resume', hint: 'Building the branded MinistryHub PDF', barWidth: '75%' },
  { stage: 'uploading', label: 'Uploading', hint: 'Storing the original CV and the new resume', barWidth: '92%' },
]
// Each stage stays on screen at least this long: reading the text takes milliseconds and would otherwise flash by.
const MIN_STAGE_MS = 700

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)))

/**
 * The add-candidate page (#/candidates/new): one CV drop zone and "Start". While sending, the drop zone
 * and the button are disabled (a double submit would pay the AI twice) and a progress card covers the page:
 * Extracting text, AI cleaning, Creating resume, Uploading, each shown for at least MIN_STAGE_MS (the stage
 * queue is copied from InterviewForm). Errors (a CV over 10 MB, the spending cap, the rate limit, the network
 * and error events) show next to the button. Beside the form, "What happens" lists the same four steps. When done,
 * it links to the new resume and the candidate page, and "Add another" brings the empty form back.
 */
export function AddCandidateForm() {
  const [stage, setStage] = useState<CandidateStage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newResumeUrl, setNewResumeUrl] = useState<string | null>(null)
  const isSubmitting = stage !== null

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const file = new FormData(event.currentTarget).get('file') as File
    if (file.size > MAX_CV_BYTES) {
      setError(FILE_TOO_LARGE_MESSAGE)
      return
    }

    setStage('reading')
    setError(null)
    // Stages are queued so each is shown for MIN_STAGE_MS; an error stops the queue and shows at once.
    let stageShownAt = Date.now()
    let stageQueue = Promise.resolve()
    let stagesStopped = false
    function showStage(next: CandidateStage) {
      stageQueue = stageQueue.then(async () => {
        await wait(MIN_STAGE_MS - (Date.now() - stageShownAt))
        if (stagesStopped) return
        setStage(next)
        stageShownAt = Date.now()
      })
    }
    try {
      const url = await addCandidate(file, showStage)
      await stageQueue
      setNewResumeUrl(url)
    } catch (caught) {
      stagesStopped = true
      if (caught instanceof ApiError) {
        // 402 and 429 carry their own message as `detail`; the 413 body is plain text.
        setError(caught.status === 413 ? FILE_TOO_LARGE_MESSAGE : caught.message)
      } else {
        // Also a stream that ended without a result.
        setError(NETWORK_ERROR_MESSAGE)
      }
    }
    setStage(null)
  }

  return (
    <>
      <section className="page-hero">
        <div className="page-container">
          <div className="hero-heading">
            <h2 className="hero-title">Add candidate</h2>
            <p className="hero-text">Drop the candidate's CV. AI reads it and creates the branded MinistryHub resume.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body candidate-add">
        {newResumeUrl ? (
          <CandidateDone newResumeUrl={newResumeUrl} onAddAnother={() => setNewResumeUrl(null)} />
        ) : (
          <form onSubmit={handleSubmit}>
            <fieldset className="candidate-fieldset" disabled={isSubmitting}>
              <CvDropzone storesFile />
              <div className="candidate-actions">
                {error && (
                  <p role="alert" className="alert alert-error">
                    {error}
                  </p>
                )}
                <p className="candidate-actions-text">
                  {isSubmitting ? 'Keep this tab open until the resume is ready.' : 'This can take up to a minute.'}
                </p>
                <button type="submit" className="button button-primary" disabled={isSubmitting}>
                  {isSubmitting ? (
                    <>
                      <span className="button-spinner" aria-hidden="true" />
                      Adding…
                    </>
                  ) : (
                    'Start'
                  )}
                </button>
              </div>
            </fieldset>
          </form>
        )}
        <aside className="card candidate-steps" aria-labelledby="candidate-steps-heading">
          <h3 id="candidate-steps-heading">What happens</h3>
          <ol>
            {PROGRESS_STEPS.map((step) => (
              <li key={step.stage}>
                <span className="candidate-step-label">{step.label}</span>
                <span className="candidate-step-hint">{step.hint}</span>
              </li>
            ))}
          </ol>
          <p className="candidate-steps-note">
            The candidate then shows on <a href="#/candidates">Candidates</a> with both files and the AI cost.
          </p>
        </aside>
        {stage && <AddProgress stage={stage} />}
      </div>
    </>
  )
}

/** The finished state: the new resume to open, the way to the candidate page, and "Add another" for the next CV. */
function CandidateDone({ newResumeUrl, onAddAnother }: { newResumeUrl: string; onAddAnother: () => void }) {
  const headingRef = useRef<HTMLHeadingElement>(null)

  // Move focus to the result, so keyboard and screen-reader users land on it.
  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  return (
    <section className="card candidate-done" aria-labelledby="candidate-done-heading">
      <span className="candidate-done-tick" aria-hidden="true">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6 9 17l-5-5" />
        </svg>
      </span>
      <h3 id="candidate-done-heading" ref={headingRef} tabIndex={-1}>
        Candidate added
      </h3>
      <p>The original CV and the new resume are stored.</p>
      <div className="candidate-done-actions">
        <a className="button button-primary" href={newResumeUrl} target="_blank" rel="noopener noreferrer">
          Open new resume
        </a>
        <a className="button button-outline" href="#/candidates">
          View candidates
        </a>
        <button type="button" className="button button-outline" onClick={onAddAnother}>
          Add another
        </button>
      </div>
    </section>
  )
}

/** The progress card shown over the whole page while sending: a bar, the four steps and the current stage. */
function AddProgress({ stage }: { stage: CandidateStage }) {
  const current = PROGRESS_STEPS.findIndex((step) => step.stage === stage)

  return (
    <div className="progress-overlay">
      <section className="card progress-card" aria-labelledby="add-progress-heading">
        <div className="progress-bar" aria-hidden="true">
          <span style={{ width: PROGRESS_STEPS[current].barWidth }} />
        </div>
        <div className="progress-body">
          <div className="progress-head">
            <h3 id="add-progress-heading">Adding the candidate</h3>
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
