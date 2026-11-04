import { useEffect, useState } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { formatFullDate, formatUsd, formatWhen } from '../../shared/format.ts'
import { deleteCandidate, listCandidates, type CandidateSummary } from './api.ts'
import './candidates.css'

const LOAD_ERROR_MESSAGE = 'Could not load the candidates.'
const DELETE_ERROR_MESSAGE = 'Could not delete the candidate. Try again.'
const DELETE_CONFIRM_MESSAGE = 'Delete this candidate and both resume files?'
const SOURCE_LABELS: Record<CandidateSummary['source'], string> = { manual: 'Manual', api: 'API', url: 'API (URL)' }

/**
 * The candidate page (#/candidates): a table of candidates, newest first, 20 at a time with "Load more".
 * Each row shows the name, the old resume (a presigned link that expires after 1 hour), the new resume (its
 * permanent public URL), the time added in the browser's time zone, the source, the AI cost ("—" when none was
 * priced) and Delete, which asks first and removes the row once the backend deletes it and both files.
 */
export function CandidateList() {
  const [items, setItems] = useState<CandidateSummary[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    // The first page replaces what's shown, so StrictMode's second load in dev doesn't duplicate rows.
    listCandidates()
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
    listCandidates(lastId)
      .then((page) => {
        setItems((shown) => (lastId === undefined ? page.items : [...shown, ...page.items]))
        setHasMore(page.has_more)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }

  async function handleDelete(id: number) {
    if (!window.confirm(DELETE_CONFIRM_MESSAGE)) return
    setDeleteError(null)
    try {
      await deleteCandidate(id)
      setItems((shown) => shown.filter((item) => item.id !== id))
    } catch (caught) {
      setDeleteError(caught instanceof ApiError ? caught.message : DELETE_ERROR_MESSAGE)
    }
  }

  const isFirstLoad = status === 'loading' && items.length === 0

  return (
    <>
      <section className="page-hero">
        <div className="page-container">
          <div className="hero-heading">
            <h2 className="hero-title">Candidates</h2>
            <p className="hero-text">Every candidate added here or through the public API, newest first.</p>
          </div>
          {items.length > 0 && (
            <p className="candidate-count">{hasMore ? `Newest ${items.length} shown` : `${items.length} ${items.length === 1 ? 'candidate' : 'candidates'}`}</p>
          )}
        </div>
      </section>
      <div className="page-container page-body">
        <section className="card candidate-list-card" aria-label="Candidates">
          {deleteError && (
            <p role="alert" className="alert alert-error">
              {deleteError}
            </p>
          )}
          {(items.length > 0 || isFirstLoad) && (
            <div className="candidate-table-wrap">
              <table className="candidate-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Resumes</th>
                    <th scope="col">Added</th>
                    <th scope="col">Source</th>
                    <th scope="col" className="num">
                      AI cost
                    </th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, index) => (
                    <tr key={item.id} className="candidate-row" style={{ animationDelay: `${Math.min(index % 20, 10) * 30}ms` }}>
                      <th scope="row">
                        <span className="candidate-person">
                          <span className="avatar" aria-hidden="true">
                            {initialsOf(item.name)}
                          </span>
                          {item.name}
                        </span>
                      </th>
                      <td>
                        <span className="candidate-resumes">
                          <a href={item.new_resume_url} target="_blank" rel="noopener noreferrer">
                            New resume
                          </a>
                          <a href={item.original_resume_url} target="_blank" rel="noopener noreferrer">
                            Original
                          </a>
                        </span>
                      </td>
                      <td className="candidate-added">
                        <time dateTime={item.created_at} title={formatFullDate(item.created_at)}>
                          {formatWhen(item.created_at)}
                        </time>
                      </td>
                      <td>
                        <span className="pill">{SOURCE_LABELS[item.source]}</span>
                      </td>
                      <td className="num candidate-cost">{formatUsd(item.ai_cost_usd)}</td>
                      <td className="candidate-row-actions">
                        <button
                          type="button"
                          className="button button-ghost"
                          aria-label={`Delete ${item.name}`}
                          title="Delete"
                          onClick={() => void handleDelete(item.id)}
                        >
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M4 7h16" />
                            <path d="M10 11v6M14 11v6" />
                            <path d="M6 7l1 13h10l1-13" />
                            <path d="M9 7V4h6v3" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  ))}
                  {/* Loading: the final layout with grey bars in place of the values, so nothing jumps when rows land. */}
                  {isFirstLoad &&
                    [0, 1, 2, 3].map((row) => (
                      <tr key={row} className="candidate-skeleton" aria-hidden="true">
                        <th scope="row">
                          <span className="candidate-person">
                            <span className="avatar" />
                            <span className="skeleton-bar" style={{ width: 140 }} />
                          </span>
                        </th>
                        <td>
                          <span className="skeleton-bar" style={{ width: 180 }} />
                        </td>
                        <td>
                          <span className="skeleton-bar" style={{ width: 130 }} />
                        </td>
                        <td>
                          <span className="skeleton-bar" style={{ width: 56 }} />
                        </td>
                        <td className="num">
                          <span className="skeleton-bar" style={{ width: 60 }} />
                        </td>
                        <td />
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
          {status === 'ready' && items.length === 0 && (
            <div className="candidate-empty">
              <h3>No candidates yet</h3>
              <p>
                Drop a CV and the branded MinistryHub resume is ready in about a minute. <a href="#/candidates/new">Add a candidate</a>.
              </p>
            </div>
          )}
          {status === 'loading' && (
            <p role="status" className={isFirstLoad ? 'sr-only' : 'candidate-message'}>
              Loading…
            </p>
          )}
          {status === 'error' && (
            <div className="candidate-footer">
              <p role="alert" className="alert alert-error">
                {LOAD_ERROR_MESSAGE}
              </p>
              <button type="button" className="button button-outline" onClick={loadNextPage}>
                Try again
              </button>
            </div>
          )}
          {status === 'ready' && hasMore && (
            <div className="candidate-footer">
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

/** "JD" for "Jane Doe": the first letters of the first and last words. */
function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  return (words[0][0] + (words.length > 1 ? words.at(-1)![0] : '')).toUpperCase()
}
