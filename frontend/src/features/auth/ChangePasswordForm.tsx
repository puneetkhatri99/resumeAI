import { useState, type FormEvent } from 'react'
import { ApiError } from '../../shared/api/client.ts'
import { changePassword, MIN_PASSWORD_LENGTH } from './api.ts'
import './auth.css'

const NETWORK_ERROR_MESSAGE = 'Could not reach the server. Check your connection and try again.'
const MISMATCH_MESSAGE = 'The new passwords don’t match.'
const TOO_SHORT_MESSAGE = `The new password needs at least ${MIN_PASSWORD_LENGTH} characters.`

/**
 * The change-password page (#/password), for every user: current password, new password and the new one again.
 * A mismatched repeat is caught here, without a request. On success it shows "Password changed." (the user's
 * other sessions end; this one stays). A wrong current password (400), a too-short password (422) and too many
 * attempts (429) show their messages; none of them logs the user out.
 */
export function ChangePasswordForm() {
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isDone, setIsDone] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const newPassword = String(form.get('new_password'))
    setIsDone(false)
    if (newPassword !== String(form.get('repeat_password'))) {
      setError(MISMATCH_MESSAGE)
      return
    }
    setIsBusy(true)
    setError(null)
    try {
      await changePassword(String(form.get('current_password')), newPassword)
      formElement.reset()
      setIsDone(true)
    } catch (caught) {
      if (!(caught instanceof ApiError)) setError(NETWORK_ERROR_MESSAGE)
      else setError(caught.status === 422 ? TOO_SHORT_MESSAGE : caught.message)
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <>
      <section className="page-hero">
        <div className="page-container">
          <div className="hero-heading">
            <h2 className="hero-title">Change password</h2>
            <p className="hero-text">Your other sessions end; this one stays logged in.</p>
          </div>
        </div>
      </section>
      <div className="page-container page-body">
        <form className="card auth-form" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field">
            <span className="field-label">Current password</span>
            <input name="current_password" type="password" autoComplete="current-password" required maxLength={1024} />
          </label>
          <label className="field">
            <span className="field-label">New password</span>
            <input
              name="new_password"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              maxLength={1024}
            />
          </label>
          <label className="field">
            <span className="field-label">Repeat new password</span>
            <input
              name="repeat_password"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              maxLength={1024}
            />
          </label>
          {error && (
            <p role="alert" className="alert alert-error">
              {error}
            </p>
          )}
          {isDone && (
            <p role="status" className="auth-notice">
              Password changed.
            </p>
          )}
          <button type="submit" className="button button-primary" disabled={isBusy}>
            {isBusy ? 'Saving…' : 'Change password'}
          </button>
        </form>
      </div>
    </>
  )
}
