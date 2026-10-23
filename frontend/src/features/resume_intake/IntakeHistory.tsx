import { useEffect, useState } from 'react'
import { formatUsd } from '../../shared/format.ts'
import { listResumeIntakes, type IntakeSummary } from './api.ts'
import './resumeIntake.css'

const WORK_MODE_LABELS: Record<IntakeSummary['work_mode'], string> = {
  remote: 'Remote',
  hybrid: 'Hybrid',
  onsite: 'On-site',
}
const LOAD_ERROR_MESSAGE = 'Could not load past interviews.'

/** "JD" for "Jane Doe": the first letter of the first two words. */
function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter((word) => word)
    .slice(0, 2)
    .map((word) => [...word][0].toUpperCase())
    .join('')
}

/**
 * The past-interviews page (#/history): stored intakes newest first, 20 at a time with "Load more".
 * Each row links to #/history/{id}, which opens the full result. A row from before AI extraction has no
 * name or email, so it shows the CV's file name. Each row also shows what its AI calls cost ("—" for intakes
 * from before AI usage was recorded).
 */
export function IntakeHistory() {
  const [items, setItems] = useState<IntakeSummary[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    // The first page replaces what's shown, so StrictMode's second load in dev doesn't duplicate rows.
    listResumeIntakes()
      .then((page) => {
        setItems(page.items)
        setHasMore(page.has_more)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }, [])

  /** "Load more", or "Try again" after an error: the page after the last row shown (or the first page). */
  function loadNextPage() {
    const lastId = items.at(-1)?.id
    setStatus('loading')
    listResumeIntakes(lastId)
      .then((page) => {
        setItems((shown) => (lastId === undefined ? page.items : [...shown, ...page.items]))
        setHasMore(page.has_more)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }

  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow">History</span>
            <h2 className="hero-title">Past interviews</h2>
            <p className="hero-text">Every submitted interview, newest first. Open one to see its full result.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <section className="card history-card">
          {items.length > 0 && (
            <ul className="history-list">
              {items.map((item) => (
                <li key={item.id}>
                  <a className="history-row" href={`#/history/${item.id}`}>
                    <span className="history-avatar" aria-hidden="true">
                      {initials(item.candidate_name || item.original_filename)}
                    </span>
                    <span className="history-who">
                      <span className="history-name">{item.candidate_name || item.original_filename}</span>
                      {item.candidate_email && <span className="history-email">{item.candidate_email}</span>}
                    </span>
                    <span className="history-date">
                      {new Date(item.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                    </span>
                    <span className="history-cost">
                      <span className="history-cost-label">AI cost</span>
                      {formatUsd(item.ai_cost_usd)}
                    </span>
                    <span className="history-badges">
                      <span className="pill pill-violet">{`${item.rating} / 5`}</span>
                      <span className="pill pill-teal">{WORK_MODE_LABELS[item.work_mode]}</span>
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          )}
          {status === 'ready' && items.length === 0 && (
            <p className="history-message">
              No interviews yet. <a href="#/">Start a new interview</a>.
            </p>
          )}
          {status === 'loading' && (
            <p role="status" className="history-message">
              Loading…
            </p>
          )}
          {status === 'error' && (
            <div className="history-footer">
              <p role="alert" className="alert alert-error">
                {LOAD_ERROR_MESSAGE}
              </p>
              <button type="button" className="button button-outline" onClick={loadNextPage}>
                Try again
              </button>
            </div>
          )}
          {status === 'ready' && hasMore && (
            <div className="history-footer">
              <button type="button" className="button button-outline" onClick={loadNextPage}>
                Load more
              </button>
            </div>
          )}
        </section>
      </div>
    </>
  )
}
