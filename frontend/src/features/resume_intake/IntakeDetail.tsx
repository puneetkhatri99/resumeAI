import { useEffect, useState } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { getResumeIntake, type ResumeIntake } from './api.ts'
import { IntakeResult } from './IntakeResult.tsx'
import './resumeIntake.css'

const NOT_FOUND_MESSAGE = "This interview doesn't exist."
const LOAD_ERROR_MESSAGE = 'Could not load this interview.'

type IntakeDetailProps = {
  intakeId: number
  /** The intake when the caller already has it (right after a submit), so it isn't fetched again. */
  initialResult: ResumeIntake | null
  /** Called when the interviewer wants an empty form for the next interview. */
  onReset: () => void
}

/**
 * The page for one stored interview (#/history/{id}): the same result screen as after a submit.
 * Uses `initialResult` when it is this intake, otherwise fetches it, showing "Loading…" meanwhile and a
 * not-found or error message with a link back to the list if that fails.
 */
export function IntakeDetail({ intakeId, initialResult, onReset }: IntakeDetailProps) {
  const known = initialResult?.id === intakeId ? initialResult : null
  // Each keeps the id it belongs to, so a reply for a previously opened intake is never shown.
  const [fetched, setFetched] = useState<ResumeIntake | null>(null)
  const [failure, setFailure] = useState<{ id: number; message: string } | null>(null)

  useEffect(() => {
    if (known) return
    let isCurrent = true
    getResumeIntake(intakeId)
      .then((intake) => isCurrent && setFetched(intake))
      .catch((error: unknown) => {
        const message = error instanceof ApiError && error.status === 404 ? NOT_FOUND_MESSAGE : LOAD_ERROR_MESSAGE
        if (isCurrent) setFailure({ id: intakeId, message })
      })
    return () => {
      isCurrent = false
    }
  }, [intakeId, known])

  const result = known ?? (fetched?.id === intakeId ? fetched : null)
  if (result) return <IntakeResult result={result} onReset={onReset} />

  const message = failure?.id === intakeId ? failure.message : null
  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow">History</span>
            <h2 className="hero-title">Past interview</h2>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <section className="card history-card">
          {message ? (
            <div className="history-footer">
              <p role="alert" className="alert alert-error">
                {message}
              </p>
              <a className="button button-outline" href="#/history">
                Back to past interviews
              </a>
            </div>
          ) : (
            <p role="status" className="history-message">
              Loading…
            </p>
          )}
        </section>
      </div>
    </>
  )
}
