import { useEffect, useState } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { formatUsd } from '../../shared/format.ts'
import { deleteCandidate, listCandidates, type CandidateSummary } from './api.ts'
import './candidates.css'

const LOAD_ERROR_MESSAGE = 'Could not load the candidates.'
const DELETE_ERROR_MESSAGE = 'Could not delete the candidate. Try again.'
const DELETE_CONFIRM_MESSAGE = 'Delete this candidate and both resume files?'
const SOURCE_LABELS: Record<CandidateSummary['source'], string> = { manual: 'Manual', api: 'API' }

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

  return (
    <>
      <section className="page-hero band">
        <div className="page-container">
          <div className="hero-heading">
            <span className="hero-eyebrow">Candidates</span>
            <h2 className="hero-title">Candidates</h2>
            <p className="hero-text">Every candidate added here or through the public API, newest first.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <section className="card candidate-card">
          {deleteError && (
            <p role="alert" className="alert alert-error">
              {deleteError}
            </p>
          )}
          {items.length > 0 && (
            <div className="candidate-table-wrap">
              <table className="candidate-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Old resume</th>
                    <th scope="col">New resume</th>
                    <th scope="col">Added</th>
                    <th scope="col">Source</th>
                    <th scope="col">AI cost</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <th scope="row">{item.name}</th>
                      <td>
                        <a href={item.original_resume_url} target="_blank" rel="noopener noreferrer">
                          Original
                        </a>
                      </td>
                      <td>
                        <a href={item.new_resume_url} target="_blank" rel="noopener noreferrer">
                          New resume
                        </a>
                      </td>
                      <td>{new Date(item.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}</td>
                      <td>{SOURCE_LABELS[item.source]}</td>
                      <td className="candidate-cost">{formatUsd(item.ai_cost_usd)}</td>
                      <td>
                        <button
                          type="button"
                          className="button button-outline"
                          aria-label={`Delete ${item.name}`}
                          onClick={() => void handleDelete(item.id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {status === 'ready' && items.length === 0 && (
            <p className="candidate-message">
              No candidates yet. <a href="#/candidates/new">Add a candidate</a>.
            </p>
          )}
          {status === 'loading' && (
            <p role="status" className="candidate-message">
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
