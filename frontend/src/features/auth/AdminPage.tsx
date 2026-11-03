import { useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import {
  addRecruiter,
  listRecruiters,
  MIN_PASSWORD_LENGTH,
  removeRecruiter,
  resetRecruiterPassword,
  type Recruiter,
} from './api.ts'
import './auth.css'

const ADMINS_ONLY_MESSAGE = 'Admins only.'
const LOAD_ERROR_MESSAGE = 'Could not load the recruiters.'
const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'
const INVALID_INPUT_MESSAGE = `Enter a valid email and a password of at least ${MIN_PASSWORD_LENGTH} characters.`

/** The message for a failed add, reset or remove: the backend's detail (409, 404), a fixed one for 422 and the network. */
function actionErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) return NETWORK_ERROR_MESSAGE
  return caught.status === 422 ? INVALID_INPUT_MESSAGE : caught.message
}

/**
 * The admin page (#/admin): every recruiter (email, added on) with "Reset password" (an inline new-password field
 * and Save; their sessions end) and "Remove" (after a confirm; the account and its sessions go), plus an
 * "Add recruiter" form (email and a password the admin sets). The admin's own account isn't listed. A recruiter who
 * opens the address gets the backend's 403, shown as "Admins only."
 */
export function AdminPage() {
  const [recruiters, setRecruiters] = useState<Recruiter[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'forbidden' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // The recruiter whose inline reset field is open.
  const [resettingId, setResettingId] = useState<number | null>(null)

  useEffect(() => {
    listRecruiters()
      .then((items) => {
        setRecruiters(items)
        setStatus('ready')
      })
      .catch((caught: unknown) => setStatus(caught instanceof ApiError && caught.status === 403 ? 'forbidden' : 'error'))
  }, [])

  /** Clear the last message, run the action, and show its success notice or its error. */
  async function runAction(action: () => Promise<string>) {
    setError(null)
    setNotice(null)
    try {
      setNotice(await action())
    } catch (caught) {
      setError(actionErrorMessage(caught))
    }
  }

  function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    void runAction(async () => {
      const added = await addRecruiter(String(form.get('email')), String(form.get('password')))
      setRecruiters((shown) => [...shown, added].sort((a, b) => a.email.localeCompare(b.email)))
      formElement.reset()
      return `Added ${added.email}.`
    })
  }

  function handleReset(event: FormEvent<HTMLFormElement>, recruiter: Recruiter) {
    event.preventDefault()
    const password = String(new FormData(event.currentTarget).get('password'))
    void runAction(async () => {
      await resetRecruiterPassword(recruiter.id, password)
      setResettingId(null)
      return `Password reset for ${recruiter.email}.`
    })
  }

  function handleRemove(recruiter: Recruiter) {
    if (!window.confirm(`Remove ${recruiter.email}? They are logged out and can't log in again.`)) return
    void runAction(async () => {
      await removeRecruiter(recruiter.id)
      setRecruiters((shown) => shown.filter((item) => item.id !== recruiter.id))
      return `Removed ${recruiter.email}.`
    })
  }

  return (
    <>
      <section className="page-hero">
        <div className="page-container">
          <div className="hero-heading">
            <h2 className="hero-title">Recruiters</h2>
            <p className="hero-text">Add recruiters, reset their passwords, or remove them.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <section className="card auth-admin">
          {status === 'forbidden' && (
            <p role="alert" className="alert alert-error">
              {ADMINS_ONLY_MESSAGE}
            </p>
          )}
          {status === 'error' && (
            <p role="alert" className="alert alert-error">
              {LOAD_ERROR_MESSAGE}
            </p>
          )}
          {status === 'loading' && <p role="status">Loading…</p>}
          {status === 'ready' && (
            <>
              {error && (
                <p role="alert" className="alert alert-error">
                  {error}
                </p>
              )}
              {notice && (
                <p role="status" className="auth-notice">
                  {notice}
                </p>
              )}
              {recruiters.length === 0 ? (
                <p>No recruiters yet.</p>
              ) : (
                <div className="auth-table-wrap">
                  <table className="auth-table">
                    <thead>
                      <tr>
                        <th scope="col">Email</th>
                        <th scope="col">Added on</th>
                        <th scope="col">
                          <span className="sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {recruiters.map((recruiter) => (
                        <tr key={recruiter.id}>
                          <th scope="row">{recruiter.email}</th>
                          <td>{new Date(recruiter.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}</td>
                          <td>
                            {resettingId === recruiter.id ? (
                              <form className="auth-row-actions" onSubmit={(event) => handleReset(event, recruiter)}>
                                <label className="field">
                                  <span className="sr-only">{`New password for ${recruiter.email}`}</span>
                                  <input
                                    name="password"
                                    type="password"
                                    autoComplete="new-password"
                                    required
                                    minLength={MIN_PASSWORD_LENGTH}
                                    maxLength={1024}
                                    placeholder="New password"
                                  />
                                </label>
                                <button type="submit" className="button button-primary">
                                  Save
                                </button>
                                <button type="button" className="button button-outline" onClick={() => setResettingId(null)}>
                                  Cancel
                                </button>
                              </form>
                            ) : (
                              <div className="auth-row-actions">
                                <button
                                  type="button"
                                  className="button button-outline"
                                  aria-label={`Reset password for ${recruiter.email}`}
                                  onClick={() => setResettingId(recruiter.id)}
                                >
                                  Reset password
                                </button>
                                <button
                                  type="button"
                                  className="button button-outline"
                                  aria-label={`Remove ${recruiter.email}`}
                                  onClick={() => handleRemove(recruiter)}
                                >
                                  Remove
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <form className="auth-form" onSubmit={handleAdd}>
                <h3>Add recruiter</h3>
                <label className="field">
                  <span className="field-label">Email</span>
                  <input name="email" type="email" autoComplete="off" required maxLength={254} />
                </label>
                <label className="field">
                  <span className="field-label">Password</span>
                  <input
                    name="password"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={MIN_PASSWORD_LENGTH}
                    maxLength={1024}
                  />
                </label>
                <button type="submit" className="button button-primary">
                  Add recruiter
                </button>
              </form>
            </>
          )}
        </section>
      </div>
    </>
  )
}
